from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Union

import chardet

from .base import BaseParser, ParsedChunk, register_parser

logger = logging.getLogger(__name__)

@register_parser
class PlainTextParser(BaseParser):
    """
    纯文本（.txt）解析器。

    按连续空行切块，每块作为一个 ParsedChunk。
    section_path 留空：纯文本无结构信息。
    """

    supported_extensions = (".txt",)
    def parse(self, source: Union[str, Path, bytes]) -> list[ParsedChunk]:
        filename = self._source_name(source)
        raw_bytes = self._read_bytes(source)

        try:
            text = _decode(raw_bytes)
            return _split_txt(text, filename)
        except Exception as exc:
            logger.warning("解析 TXT 文件 '%s' 失败: %s", filename, exc)
            return [ParsedChunk(text="", metadata={"source_file": filename, "content_type": "error", "section_path": "", "error": str(exc)})]

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
        
def _split_txt(text: str, filename: str) -> list[ParsedChunk]:
    """按连续空行切块，保留块内换行（行尾空白清理即可）。"""
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[ParsedChunk] = []
    for para in paragraphs:
        clean = "\n".join(line.rstrip() for line in para.splitlines()).strip()
        if clean:
            chunks.append(ParsedChunk(
                text=clean,
                metadata={
                    "source_file": filename,
                    "content_type": "text",
                    "section_path": "",
                }
            ))
    return chunks