from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

import chardet

from .base import BaseParser, ParsedChunk

logger = logging.getLogger(__name__)

class FallbackParser(BaseParser):
    """通用兜底解析器

    用途：pipeline 遇到注册表里没有的扩展名时，显式实例化此类，
    避免抛异常导致整批文档中断。

    策略：
    - 不做任何结构解析，原文作为单个 chunk 返回
    """

    supported_extensions = ()

    def parse(self, source: Union[str, Path, bytes]) -> list[ParsedChunk]:
        filename = self._source_name(source)
        raw_bytes = self._read_bytes(source)

        if _is_binary(raw_bytes):
            logger.warning("FallbackParser 跳过二进制文件 '%s'", filename)
            return []
        
        try:
            text = _decode(raw_bytes)
            if not text.strip():
                return []
            return [ParsedChunk(
                text=text,
                metadata={
                    "source_file": filename,
                    "content_type": "text",
                    "section_path": "",
                    "parser": "fallback",
                }
            )]
        except Exception as exc:
            logger.warning("FallbackParser 解析 '%s' 失败: %s", filename, exc)
            return [ParsedChunk(text="", metadata={"source_file": filename, "content_type": "error", "section_path": "", "error": str(exc), "parser": "fallback"})]


def _is_binary(raw: bytes) -> bool:
    """检查前 8 KB 是否含 null byte，是则判定为二进制文件。"""
    return b"\x00" in raw[:8192]


def _decode(raw: bytes) -> str:
    detected = chardet.detect(raw)
    encoding = detected.get("encoding") or "utf-8"
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")
