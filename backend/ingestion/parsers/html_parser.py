from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Union

import chardet
from bs4 import BeautifulSoup, Comment, Tag

from .base import BaseParser, ParsedChunk, register_parser

logger = logging.getLogger(__name__)

# 静默丢弃：导航、交互控件、脚本——对 RAG 无价值
_REMOVE_TAGS = {"script", "style", "nav", "aside", "form", "button", "label", "iframe", "noscript", "header", "footer"}

# 块级元素：遇到时输出当前累积文本，开始新块
_BLOCK_TAGS = {
    "p", "div", "article", "section", "main", "blockquote",
    "pre", "code", "li", "dd", "dt", "figcaption", "address",
}

# h1-h6 → heading_level
_HEADING_RE = re.compile(r"^h([1-6])$")

@register_parser
class HtmlParser(BaseParser):
    """HTML 文档解析器。

    核心思路：
    - BeautifulSoup + html5lib 后端：容错最好，自动补全残缺标签
    - nav/aside/form 静默丢弃：过滤导航噪声
    - 标题层级栈 → section_path（与 WordParser 接口对齐）
    - 表格保留 HTML 字符串:不被 get_text() 摧毁
    - chardet 编码检测：企业文档编码复杂
    """
    supported_extensions = (".html", ".htm")

    def parse(
        self,
        source: Union[str, Path, bytes],
    ) -> list[ParsedChunk]:
        filename = self._source_name(source)
        raw_bytes = self._read_bytes(source)

        try:
            html_text = _decode(raw_bytes)
            soup = _make_soup(html_text)
            return _extract(soup, filename)
        except Exception as exc:
            logger.warning("解析 HTML 文件 '%s' 失败: %s", filename, exc)
            return [ParsedChunk(text="", metadata={"source_file": filename, "content_type": "error", "section_path": "", "error": str(exc)})]
        
def _extract(soup: BeautifulSoup, filename: str) -> list[ParsedChunk]:
    _clean(soup)

    chunks: list[ParsedChunk] = []
    heading_stack: dict[int, str] = {}

    # find_all 只返回感兴趣的块级/标题/表格元素，按文档顺序排列。
    # 对于嵌套块（如 div > p），find_all 会同时返回 div 和 p；
    # 用 _is_leaf_block 过滤掉容器，只保留叶子块，避免文字重复提取。
    target_tags = list(_BLOCK_TAGS) + ["h1","h2","h3","h4","h5","h6","table"]
    root = soup.body or soup

    for element in root.find_all(target_tags):
        tag = element.name.lower()

        # 标题
        m = _HEADING_RE.match(tag)
        if m:
            text = _clean_text(element.get_text())
            if not text:
                continue
            level = int(m.group(1))
            for k in list(heading_stack.keys()):
                if k >= level:
                    del heading_stack[k]
            heading_stack[level] = text
            chunks.append(ParsedChunk(
                text=text,
                metadata={
                    "source_file": filename,
                    "content_type": "title",
                    "heading_level": level,
                    "section_path": _build_section_path(heading_stack),
                    "tag": tag,
                }
            ))
            continue

        # 表格
        if tag == "table":
            # 跳过嵌套在其他表格内的子表格，避免重复
            if element.find_parent("table"):
                continue
            plain = _clean_text(element.get_text())
            if not plain:
                continue
            chunks.append(ParsedChunk(
                text=str(element),
                metadata={
                    "source_file": filename,
                    "content_type": "table",
                    "section_path": _build_section_path(heading_stack),
                    "plain_text": plain,
                }
            ))
            continue

        if tag in _BLOCK_TAGS and _is_leaf_block(element):
            text = _clean_text(element.get_text())
            if not text:
                continue
            chunks.append(ParsedChunk(
                text=text,
                metadata={
                    "source_file": filename,
                    "content_type": "text",
                    "section_path": _build_section_path(heading_stack),
                    "tag": tag,
                },
            ))
    return chunks

def _decode(raw: bytes) -> str:
    """chardet 检测编码，fallback utf-8 → latin-1。"""
    detected = chardet.detect(raw)
    encoding = detected.get("encoding") or "utf-8"

    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")
        
def _make_soup(html_text: str) -> BeautifulSoup:
    """html5lib 后端：容错最好，自动补全残缺标签。"""
    return BeautifulSoup(html_text, "html5lib")

def _clean(soup: BeautifulSoup) -> None:
    """原地清洗：删除噪声标签、注释、内联样式。"""
    remove = set(_REMOVE_TAGS)

    for tag in soup.find_all(list(remove)):
        tag.decompose()

    # 删除 HTML 注释
    for comment in soup.find_all(string= lambda t : isinstance(t, Comment)):
        comment.extract()
    
    # 删除内联 style 属性
    for tag in soup.find_all(True):
        tag.attrs.pop("style", None)

def _clean_text(text: str) -> str:
    """规范化空白字符（\\xa0、\\u2002 等），与 WordParser 保持一致。"""
    return re.sub(r"\s+", " ", text).strip()

def _build_section_path(heading_stack: dict[int, str]) -> str:
    """与 WordParser._build_section_path 相同逻辑，保持 pipeline 接口一致。"""
    if not heading_stack:
        return ""
    return " > ".join(heading_stack[k] for k in sorted(heading_stack))

def _is_leaf_block(element: Tag) -> bool:
    """判断元素是否为叶子块（不含块级子元素）。

    只有叶子块才提取文字，容器块跳过，避免重复。
    """
    for child in element.children:
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name in _BLOCK_TAGS or _HEADING_RE.match(name) or name == "table":
            return False
    return True