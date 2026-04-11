from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Union

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .base import BaseParser, ParsedChunk, register_parser

logger = logging.getLogger(__name__)

_STYLE_ID_HEADING_RE = re.compile(r"Heading(\d+)")

@register_parser
class WordParser(BaseParser):
    """
    Word 文档（.docx）解析器。

    核心思路：
    1. 通过 styleId识别标题，兼容多语言 Office
    2. 维护标题层级栈，为每个块记录 section_path（如 "第一章 > 1.1 节"）
    3. 表格转换："表头: 值; 表头: 值"——比 HTML 更适合 LLM
    4. 图片跳过（待后续扩展）
    """

    supported_extensions = (".docx",)

    def parse(self, source: Union[str, Path, bytes]) -> list[ParsedChunk]:
        filename = self._source_name(source)
        raw_bytes = self._read_bytes(source)

        try:
            doc = Document(io.BytesIO(raw_bytes))
            return self._extract(doc, filename)
        except Exception as exc:
            logger.warning("解析 Word 文件 '%s' 失败: %s", filename, exc)
            return [ParsedChunk(text="", metadata={"source_file": filename, "error": str(exc)})]

    def _extract(self, doc: Document, filename: str) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []
        heading_stack: dict[int, str] = {} # {level: heading_text}

        for block in doc.element.body:
            tag = block.tag.split("}")[-1]  # 去掉命名空间前缀

            if tag == "p":
                paragraph = Paragraph(block, doc)
                chunk = self._handle_paragraph(paragraph, filename, heading_stack)
                if chunk:
                    chunks.append(chunk)

            elif tag == "tbl":
                table = Table(block, doc)
                section_path = _build_section_path(heading_stack)
                chunk = self._handle_table(table, filename, section_path)
                if chunk:
                    chunks.append(chunk)
            
            # TODO(Step 8): 处理段落内嵌图片（w:drawing / w:pict）
            # 方案：XPath 提取图片 blob（参考 RAGflow get_picture()），
            # 上传到对象存储后输出 content_type="figure" 的 ParsedChunk。
            # 当前跳过，等 Step 8 存储层确定后再实现。
        
        return chunks
    
    def _handle_paragraph(
        self,
        paragraph: Paragraph,
        filename: str,
        heading_stack: dict[int, str]
    ) -> ParsedChunk | None:
        text = re.sub(r"\s+", " ", paragraph.text).strip()
        if not text:
            return None
        
        # 用 styleId 识别标题，兼容中文/多语言 Office
        try:
            style_id = paragraph.style.element.get(qn("w:styleId"), "")
        except Exception:
            style_id = ""
        heading_match = _STYLE_ID_HEADING_RE.match(style_id)

        if heading_match:
            level = int(heading_match.group(1))

            # 更新标题栈：当前层级及以下全部清除
            for k in list(heading_stack.keys()):
                if k >= level:
                    del heading_stack[k]
            heading_stack[level] = text
            content_type = "title"
        else:
            content_type = "text"

        return ParsedChunk(
            text=text,
            metadata={
                "source_file": filename,
                "content_type": content_type,
                "style": paragraph.style.name if paragraph.style else "Normal",
                "section_path": _build_section_path(heading_stack),
                **({"heading_level": int(heading_match.group(1))} if heading_match else {}),
            }
        )

    def _handle_table(
        self,
        table: Table,
        filename: str,
        section_path: str,
    ) -> ParsedChunk | None:
        text = _table_to_text(table)
        if not text.strip():
            return None
        
        return ParsedChunk(
            text=text,
            metadata={
                "source_file": filename,
                "content_type": "table",
                "section_path": section_path,
            }
        )
    
def _build_section_path(heading_stack: dict[int, str]) -> str:
    """
    将标题栈拼成 'Chapter 1 > Section 1.1' 风格的路径。帮助向量检索时定位语义位置。
    """
    if not heading_stack:
        return ""
    return " > ".join(heading_stack[k] for k in sorted(heading_stack))

def _is_row_bold(row_cells: list) -> bool:
    """判断一行是否含有显式加粗的 run。

    只检测run 级别显式设置会漏判，此时回退到竖线拼接，
    对 RAG 来说竖线格式同样可用，不影响检索质量。
    """
    for cell in row_cells:
        for para in cell.paragraphs:
            for run in para.runs:
                if run.bold is True:
                    return True
    return False


def _table_to_text(table: Table) -> str:
    """
    将表格转为文本。

    表头判定优先级：
      1. 首行有加粗 run → 判定为表头
      2. 首行全空      → 无表头，竖线拼接兜底
      3. 其他          → 默认首行为表头，但若键值对生成为空则退回竖线拼接

    有表头示例：
        "Product: Widget A; Quantity: 100; Price: $9.99"
    无表头回退：
        "Widget A | 100 | $9.99"
    """
    if not table.rows:
        return ""

    raw_rows = table.rows

    def cell_text(cell) -> str:
        return re.sub(r"\s+", " ", cell.text).strip()

    rows_text = [[cell_text(c) for c in row.cells] for row in raw_rows]
    rows_text = [r for r in rows_text if any(r)]  # 跳过全空行

    if not rows_text:
        return ""
    if len(rows_text) == 1:
        return " | ".join(c for c in rows_text[0] if c)

    # 表头判定
    first_row_cells = list(raw_rows[0].cells)
    has_bold_header = _is_row_bold(first_row_cells)
    headers = rows_text[0]
    data_rows = rows_text[1:]

    # 只有检测到加粗表头才生成键值对；否则一律竖线拼接
    # 原因：强行猜测表头比错误的键值对更危险（会让 LLM 误解数据关系）
    if not has_bold_header:
        return "\n".join(" | ".join(c for c in row if c) for row in rows_text)

    lines = []
    for row in data_rows:
        pairs = []
        seen: set[tuple[str, str]] = set()
        for header, value in zip(headers, row):
            if header and (header, value) not in seen:
                pairs.append(f"{header}: {value}")
                seen.add((header, value))
        if pairs:
            lines.append("; ".join(pairs))

    if not lines:
        # 键值对生成为空（如全是合并单元格），退回竖线拼接
        return "\n".join(" | ".join(c for c in row if c) for row in rows_text)

    return "\n".join(lines)

