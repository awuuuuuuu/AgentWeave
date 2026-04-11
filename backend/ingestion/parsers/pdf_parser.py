from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal, Union

import fitz # pymupdf

from .base import BaseParser, ParsedChunk, register_parser

logger = logging.getLogger(__name__)

def _get_api_url() -> str:
    return os.getenv("UNSTRUCTURED_API_URL", "")

def _get_api_key() -> str:
    return os.getenv("UNSTRUCTURED_API_KEY", "")

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
            return [ParsedChunk(text="", metadata={"source_file": filename, "error": str(exc)})]

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
    
    def _run_smart_pipeline(self, pdf_bytes: bytes, filename: str) -> list[ParsedChunk]:
        """
        智能探路算法：
        1. fitz 直接提取文字
        2. 若某页存在【空页/乱码/图片】的话，则将该页加入降级的页码集合
        """
        chunks = []

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_idx in range(doc.page_count):
                page_num = page_idx + 1
                page = doc[page_idx]
                
                # 1. 提取当前页并检查
                page_elements = []
                page_has_image = False
                page_raw_text = ""
                
                page_area = page.rect.width * page.rect.height
                for block in page.get_text("blocks"):
                    if block[6] != 0:
                        continue  # 内联图片块，由下面 get_image_info 统一处理
                    text = block[4].strip()
                    if text:
                        page_raw_text += text
                        page_elements.append(_FitzElement(text, page_num, bbox=block[:4]))

                # 检测页面上所有光栅图像（含 XObject 嵌入图，如论文配图）
                for img_info in page.get_image_info():
                    bbox = img_info["bbox"]
                    img_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                    if page_area > 0 and img_area / page_area > 0.1:
                        page_has_image = True
                        break
                
                # 2. 判断是否降级
                needs_fallback = False
                if page_has_image:
                    logger.debug("Page %d: 检测到图片/图表，加入降级名单。", page_num)
                    needs_fallback = True
                elif not page_raw_text.strip():
                    logger.debug("Page %d: 全页无文字(扫描页)，加入降级名单。", page_num)
                    needs_fallback = True
                elif _is_garbled(page_raw_text):
                    logger.debug("Page %d: 乱码比例过高(编码损坏)，加入降级名单。", page_num)
                    needs_fallback = True

                # 3. 路由执行
                if not needs_fallback:
                    # 正常页：组装本地提取的结果
                    for el in page_elements:
                        chunk = self._build_chunk(el, filename)
                        if chunk: chunks.append(chunk)
                else:
                    # 异常页面处理：复用当前 Document 实例截取单页，调用远端高精度解析服务兜底
                    logger.info("第 %d 页命中降级规则 (存在图表/空页/乱码)，触发 hi_res 远端 API 进行高精度重解析...", page_num)
                    try:
                        single_page_bytes = self._extract_single_page_from_doc(doc, page_num)
                        hi_res_elements = _call_api(
                            single_page_bytes, None, "hi_res", infer_table_structure=True
                        )
                        for el in hi_res_elements:
                            chunk = self._build_chunk(el, filename)
                            if chunk:
                                chunk.metadata["fallback"] = "hi_res"
                                chunks.append(chunk)
                    except Exception as exc:
                        logger.warning("对第 %d 页执行远端降级失败: %s", page_num, exc)
                        # 降级失败，使用本地提取的残缺版
                        for el in page_elements:
                            chunk = self._build_chunk(el, filename)
                            if chunk: chunks.append(chunk)
                            
        return chunks
    
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