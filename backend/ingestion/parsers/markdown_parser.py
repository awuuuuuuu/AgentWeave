from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Union

import chardet

from .base import BaseParser, ParsedChunk, register_parser

logger = logging.getLogger(__name__)

# Markdown 标题行：# / ## / ### …（最多 6 级）
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")

# YAML frontmatter 块：文件开头 --- 到下一个 ---
_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)

@register_parser
class MarkdownParser(BaseParser):
    """Markdown（.md / .markdown）解析器。

    核心思路：
    1. 提取并保留 YAML frontmatter 键值对到 metadata
    2. 按标题（# ## ###…）切块，维护 section_path
    3. 标题本身作为独立 title chunk，后续段落归属到该章节
    """

    supported_extensions = (".md", ".markdown")

    def parse(self, source: Union[str, Path, bytes]) -> list[ParsedChunk]:
        filename = self._source_name(source)
        raw_bytes = self._read_bytes(source)

        try:
            text = _decode(raw_bytes)
            frontmatter, body = _split_frontmatter(text)
            return _split_md(body, filename, frontmatter)
        except Exception as exc:
            logger.warning("解析 Markdown 文件 '%s' 失败: %s", filename, exc)
            return [ParsedChunk(text="", metadata={"source_file": filename, "error": str(exc)})]

def _decode(raw: bytes) -> str:
    """chardet 检测编码，fallback utf-8 → latin-1。与 HtmlParser 保持一致。"""
    detected = chardet.detect(raw)
    encoding = detected.get("encoding") or "utf-8"
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """
    提取 YAML frontmatter，返回 (键值字典, 正文)。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    
    frontmatter: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            k = key.strip()
            v = value.strip().strip('"').strip("'")
            if k:
                frontmatter[k] = v
    body = text[m.end():]
    return frontmatter, body

def _split_md(body: str, filename: str, frontmatter: dict[str, str]) -> list[ParsedChunk]:
    """按 Markdown 标题切块，维护 section_path。

    每个标题生成一个 title chunk；标题下的段落合并为一个 text chunk，保留换行。
    状态机跟踪代码块（```），防止代码内的 # 注释被误识别为标题。
    """
    chunks: list[ParsedChunk] = []
    heading_stack: dict[int, str] = {}
    pending_lines: list[str] = []
    in_code_block = False
    current_section_path = ""

    def flush(section_path: str) -> None:
        block = "\n".join(pending_lines).strip()
        pending_lines.clear()
        if block:
            chunks.append(ParsedChunk(
                text=block,
                metadata={
                    "source_file": filename,
                    "content_type": "text",
                    "section_path": section_path,
                    **frontmatter,
                }
            ))

    for line in body.splitlines():
        # 代码块开关（``` 或 ~~~）
        if line.strip().startswith("```") or line.strip().startswith("~~~"):
            in_code_block = not in_code_block
            pending_lines.append(line)
            continue

        # 代码块内：原样保留，不做标题匹配
        if in_code_block:
            pending_lines.append(line)
            continue

        m = _MD_HEADING_RE.match(line)
        if m:
            flush(current_section_path)

            level = len(m.group(1))
            heading_text = m.group(2).strip()
            for k in list(heading_stack.keys()):
                if k >= level:
                    del heading_stack[k]
            heading_stack[level] = heading_text
            current_section_path = _build_section_path(heading_stack)

            chunks.append(ParsedChunk(
                text=heading_text,
                metadata={
                    "source_file": filename,
                    "content_type": "title",
                    "heading_level": level,
                    "section_path": current_section_path,
                    **frontmatter,
                }
            ))
        else:
            pending_lines.append(line)
    flush(current_section_path)
    return chunks

def _build_section_path(heading_stack: dict[int, str]) -> str:
    if not heading_stack:
        return ""
    
    return " > ".join(heading_stack[k] for k in sorted(heading_stack))