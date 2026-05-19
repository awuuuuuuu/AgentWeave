from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal, Union

import fitz # pymupdf

from config import settings
from .base import BaseParser, ParsedChunk, register_parser

logger = logging.getLogger(__name__)

def _get_api_url() -> str:
    return settings.unstructured_api_url

def _get_api_key() -> str:
    return settings.unstructured_api_key

Strategy = Literal["fast", "smart", "hi_res"]

# unstructured element.category → ParsedChunk.content_type
# None 表示过滤（页眉 / 页脚 / 页码）
_CATEGORY_MAP: dict[str, str | None] = {
    "Title": "title",
    "NarrativeText": "text",
    "Text": "text",
    "ListItem": "text",
    "UncategorizedText": "text",
    "Table": "table",
    "Figure": "figure",
    "Image": "figure",
    "FigureCaption": "figure",
    "Header": None,
    "Footer": None,
    "PageNumber": None,
    "EmailAddress": "text",
    "Address": "text",
    "Formula": "text",
    "CodeSnippet": "text",
}

@register_parser
class PdfParser(BaseParser):
    """PDF 文档解析器。

    strategy 决策逻辑：
        "fast":
            → 只使用fitz提取文字
        "smart":（智能路由档）
            → fitz 直接提取文字
            → 若某页满足【空页】、【乱码比例过高】、【存在重要图表】条件的话，进入降级策略
            → 将该单页切片，发送给远端 hi_res API 进行深度 OCR 兜底
        "hi_res":
            → 调用远端 Unstructured API
    """

    supported_extensions = (".pdf",)

    def parse(
            self,
            source: Union[str, Path, bytes],
            strategy: Strategy = "smart"
    ) -> list[ParsedChunk]:
        """解析 PDF，返回 ParsedChunk 列表。

        Args:
            source:   文件路径或字节流
            strategy: "fast" | "smart" | "hi_res"
        """

        if strategy != "fast" and not _get_api_url():
            raise EnvironmentError(
                f"当前策略 strategy='{strategy}' 需要配置 UNSTRUCTURED_API_URL 环境变量。"
                "请运行 Unstructured API 服务端并设置该变量，"
                "或者使用 strategy='fast' 进行纯本地解析。"
            )
        
        filename = self._source_name(source)
        pdf_bytes = self._read_bytes(source)
        filename_arg = str(source) if not isinstance(source, bytes) else "document.pdf"

        try:
            if strategy == "fast":
                return self._run_fast_pipeline(pdf_bytes, filename)
                
            elif strategy == "smart":
                return self._run_smart_pipeline(pdf_bytes, filename)
                
            elif strategy == "hi_res":
                return self._run_hi_res_pipeline(pdf_bytes, filename_arg, filename)
                
            else:
                raise ValueError(f"未知的解析策略: {strategy}")

        except Exception as exc:
            logger.warning("解析 PDF 文件 '%s' 时发生全局崩溃: %s", filename, exc)
            return [ParsedChunk(text="", metadata={"source_file": filename, "content_type": "error", "section_path": "", "error": str(exc)})]

    def _run_fast_pipeline(self, pdf_bytes: bytes, filename: str) -> list[ParsedChunk]:
        chunks = []
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_idx in range(doc.page_count):
                page_num = page_idx + 1
                for block in doc[page_idx].get_text("blocks"):
                    if block[6] != 0: continue  # 跳过图片
                    
                    text = block[4].strip()
                    if text:
                        el = _FitzElement(text, page_num, bbox=block[:4])
                        chunk = self._build_chunk(el, filename)
                        if chunk: chunks.append(chunk)
        return chunks
    
    def _run_hi_res_pipeline(self, pdf_bytes: bytes, filename_arg: str | None, filename: str) -> list[ParsedChunk]:
        chunks = []
        elements = _call_api(pdf_bytes, filename_arg, "hi_res", infer_table_structure=True)
        for el in elements:
            chunk = self._build_chunk(el, filename)
            if chunk: chunks.append(chunk)
        return chunks
    
    # 降级页比例超过此阈值时，放弃逐页降级，直接对整份 PDF 做整体 hi_res 解析
    SMART_ESCALATE_THRESHOLD: float = 0.5
    # 局部降级时并发调用 hi_res API 的最大线程数
    SMART_MAX_WORKERS: int = 4

    def _run_smart_pipeline(self, pdf_bytes: bytes, filename: str) -> list[ParsedChunk]:
        """
        智能探路算法（两阶段）：

        Phase 1 — 本地扫描（无 API 调用）：
            fitz 全文扫描，识别每页是否需要降级（空页/乱码/含重要图表），
            同时缓存正常页的本地提取结果。

        Phase 2 — 路由决策：
            - 降级页比例 >= SMART_ESCALATE_THRESHOLD（默认 50%）：
              整体 hi_res（1 次 API 调用），跨页上下文更完整，API 调用次数最少
            - 否则：
              仅对降级页逐一调用 hi_res API，正常页沿用本地结果，节省成本
        """
        # Phase 1: 全文扫描，不调任何 API
        # 同时预提取降级页的单页 bytes，供后续并发调用使用（fitz 操作，纯内存，极快）
        good_page_chunks: dict[int, list[ParsedChunk]] = {}
        fallback_pages: list[int] = []
        fallback_page_bytes: dict[int, bytes] = {}
        fallback_page_elements: dict[int, list] = {}  # 降级 API 失败时的兜底

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            total_pages = doc.page_count
            for page_idx in range(total_pages):
                page_num = page_idx + 1
                page = doc[page_idx]

                page_elements = []
                page_has_image = False
                page_raw_text = ""

                page_area = page.rect.width * page.rect.height
                for block in page.get_text("blocks"):
                    if block[6] != 0:
                        continue
                    text = block[4].strip()
                    if text:
                        page_raw_text += text
                        page_elements.append(_FitzElement(text, page_num, bbox=block[:4]))

                for img_info in page.get_image_info():
                    bbox = img_info["bbox"]
                    img_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                    if page_area > 0 and img_area / page_area > 0.1:
                        page_has_image = True
                        break

                if page_has_image:
                    logger.debug("Page %d: 检测到图片/图表，加入降级名单。", page_num)
                    needs_fallback = True
                elif not page_raw_text.strip():
                    logger.debug("Page %d: 全页无文字(扫描页)，加入降级名单。", page_num)
                    needs_fallback = True
                elif _is_garbled(page_raw_text):
                    logger.debug("Page %d: 乱码比例过高(编码损坏)，加入降级名单。", page_num)
                    needs_fallback = True
                else:
                    needs_fallback = False

                if needs_fallback:
                    fallback_pages.append(page_num)
                    fallback_page_elements[page_num] = page_elements
                    fallback_page_bytes[page_num] = self._extract_single_page_from_doc(doc, page_num)
                else:
                    page_chunks = []
                    for el in page_elements:
                        chunk = self._build_chunk(el, filename)
                        if chunk:
                            page_chunks.append(chunk)
                    good_page_chunks[page_num] = page_chunks

        # Phase 2: 路由决策
        fallback_ratio = len(fallback_pages) / total_pages if total_pages > 0 else 0

        if fallback_ratio >= self.SMART_ESCALATE_THRESHOLD:
            logger.info(
                "降级页比例 %.0f%% (%d/%d 页) 超过阈值 %.0f%%，升级为整体 hi_res 解析（1 次 API 调用）",
                fallback_ratio * 100, len(fallback_pages), total_pages,
                self.SMART_ESCALATE_THRESHOLD * 100,
            )
            return self._run_hi_res_pipeline(pdf_bytes, None, filename)

        # 局部降级：并发调用 hi_res API，各页独立互不依赖
        logger.info(
            "降级页 %d/%d 页（%.0f%%），以 %d 线程并发调用 hi_res API",
            len(fallback_pages), total_pages, fallback_ratio * 100, self.SMART_MAX_WORKERS,
        )
        all_chunks: list[ParsedChunk] = []
        for page_chunks in good_page_chunks.values():
            all_chunks.extend(page_chunks)

        def _process_fallback_page(page_num: int) -> tuple[int, list[ParsedChunk]]:
            logger.info("第 %d 页命中降级规则 (存在图表/空页/乱码)，触发 hi_res 远端 API 进行高精度重解析...", page_num)
            try:
                hi_res_elements = _call_api(
                    fallback_page_bytes[page_num], None, "hi_res", infer_table_structure=True
                )
                chunks = []
                for el in hi_res_elements:
                    chunk = self._build_chunk(el, filename)
                    if chunk:
                        chunk.metadata["fallback"] = "hi_res"
                        chunks.append(chunk)
                return page_num, chunks
            except Exception as exc:
                logger.warning("对第 %d 页执行远端降级失败: %s", page_num, exc)
                chunks = []
                for el in fallback_page_elements.get(page_num, []):
                    chunk = self._build_chunk(el, filename)
                    if chunk:
                        chunks.append(chunk)
                return page_num, chunks

        with ThreadPoolExecutor(max_workers=self.SMART_MAX_WORKERS) as executor:
            futures = {executor.submit(_process_fallback_page, pn): pn for pn in fallback_pages}
            for future in as_completed(futures):
                _, page_chunks = future.result()
                all_chunks.extend(page_chunks)

        all_chunks.sort(key=lambda c: c.metadata.get("page_number") or 0)
        return all_chunks
    
    def _build_chunk(self, el, filename: str) -> ParsedChunk | None:
        """统一的数据装配流水线：负责过滤页眉页脚，组装最终的 ParsedChunk。"""
        content_type = _CATEGORY_MAP.get(el.category, "text")
        if content_type is None:
            return None  # 过滤掉 Header, Footer 等
            
        text = _extract_element_text(el)
        if not text.strip():
            return None

        return ParsedChunk(
            text=text,
            metadata={
                "source_file": filename,
                "page_number": getattr(el.metadata, "page_number", None) or 0,
                "content_type": content_type,
                "section_path": "",
                "element_id": getattr(el, "id", None),
                "category": el.category,
                "bbox": _extract_bbox(el),
            },
        )
    
    @staticmethod
    def _extract_single_page_from_doc(src_doc: fitz.Document, page_num: int) -> bytes:
        """从【已打开】的文档对象中截取单页。极大地节省了 I/O 耗时。"""
        with fitz.open() as dst:
            dst.insert_pdf(src_doc, from_page=page_num - 1, to_page=page_num - 1)
            return dst.tobytes()

class _FitzCoords:
    __slots__ = ("points",)

    def __init__(self, bbox: tuple[float, float, float, float]) -> None:
        x0, y0, x1, y1 = bbox
        self.points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        

class _FitzMeta:
    __slots__ = ("page_number", "coordinates", "text_as_html")
    
    def __init__(self, page_number: int, bbox: tuple | None) -> None:
        self.page_number = page_number
        self.coordinates = _FitzCoords(bbox) if bbox else None
        self.text_as_html = None
        

class _FitzElement:
    """fitz 文字块包装，接口与 unstructured Element 对齐。"""
    __slots__ = ("category", "metadata", "_text", "id")

    def __init__(self, text: str, page_number: int, bbox: tuple | None = None) -> None:
        self.category = "NarrativeText"
        self._text = text
        self.metadata = _FitzMeta(page_number=page_number, bbox=bbox)
        self.id = None
    
    def __str__(self) -> str:
        return self._text
    
def _is_garbled(text: str, threshold: float = 0.25) -> bool:
    """乱码检测器：判断提取出的文本是否为乱码（编码损坏）。

    扫描版 PDF 用 ghostscript/ghostpdl 转换后，文字层编码可能损坏，
    表现为大量 Unicode 替换字符（\ufffd）或不可打印控制字符。

    判断：不可打印字符数 / 总字符数 > threshold（默认 25%）。
    不可打印字符定义：
      - \ufffd（Unicode 替换字符，编码出错时出现）
      - ASCII 控制字符（ord < 0x20），排除 \n \r \t（合法换行/制表）
    """

    if not text:
        return False
    bad = sum(
        1 for c in text
        if c == "\ufffd" or (ord(c) < 0x20 and c not in "\n\r\t")
    )
    return bad / len(text) > threshold


def _call_api(pdf_bytes: bytes, filename_arg: str | None, strategy: str, **kwargs):
    """调用远端 Unstructured API"""
    from unstructured.partition.api import partition_via_api
    return partition_via_api(
        file=pdf_bytes,
        metadata_filename=filename_arg or "document.pdf",
        strategy=strategy,
        api_url=_get_api_url(),
        api_key=_get_api_key() or None,
        **kwargs,
    )

def _extract_element_text(el) -> str:
    """提取文字，优先保留表格的 HTML 结构。"""
    if el.category == "Table":
        html = getattr(el.metadata, "text_as_html", None)
        if html:
            return html
    return str(el)

def _extract_bbox(el) -> tuple[float, float, float, float] | None:
    """提取矩形边界框坐标。"""
    coords = getattr(el.metadata, "coordinates", None)
    if not coords: return None
    points = getattr(coords, "points", None)
    if not points or len(points) < 4: return None
    return (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )