from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Union

import chardet

from .base import BaseParser, ParsedChunk, register_parser

logger = logging.getLogger(__name__)

_HEADER_SCAN_ROWS = 10

@register_parser
class CsvParser(BaseParser):
    """CSV 文件解析器。

    核心思路：
    1. chardet 自动检测编码
    2. 启发式表头检测：扫描前 10 行，选非空列最多的行作为表头
    3. 每数据行 → 一个 chunk，格式：`字段: 值; 字段: 值`
    """

    supported_extensions = (".csv",)

    def parse(self, source: Union[str, Path, bytes]) -> list[ParsedChunk]:
        filename = self._source_name(source)
        raw_bytes = self._read_bytes(source)

        try:
            text = _decode(raw_bytes)
            return _parse_csv(text, filename)
        except Exception as exc:
            logger.warning("解析 CSV 文件 '%s' 失败: %s", filename, exc)
            return [ParsedChunk(text="", metadata={"source_file": filename, "error": str(exc)})]

def _decode(raw: bytes) -> str:
    """chardet 检测编码，处理 UTF-8-BOM，fallback utf-8 → latin-1。"""
    # 优先剥离 BOM（Excel 导出的 CSV 经常带 BOM）
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    
    detected = chardet.detect(raw)
    encoding = detected.get("encoding") or "utf-8"

    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")
        
def _parse_csv(text: str, filename: str) -> list[ParsedChunk]:
    io_stream = io.StringIO(text)
    sample = io_stream.read(4096)
    io_stream.seek(0)

    try:
        dialect = csv.Sniffer().sniff(sample)
        reader = csv.reader(io_stream, dialect=dialect)
    except csv.Error:
        reader = csv.reader(io_stream)
    
    rows = list(reader)
    if not rows:
        return []

    header_idx = _detect_header(rows)
    preamble = _extract_preamble(rows, header_idx) if header_idx > 0 else {}
    
    if header_idx == -1:
        headers = [f"col_{i}" for i in range(len(rows[0]))]
        data_rows = rows
    else:
        headers = [cell.strip() or f"col_{i}" for i, cell in enumerate(rows[header_idx])]
        data_rows = rows[header_idx + 1:]

    chunks: list[ParsedChunk] = []
    for row_num, row in enumerate(data_rows, start=header_idx + 2):
        if not any(cell.strip() for cell in row):
            continue
        
        text_out = _row_to_text(headers, row)
        if text_out:
            chunks.append(ParsedChunk(
                text=text_out,
                metadata={
                    "source_file": filename,
                    "content_type": "table",
                    "section_path": "",
                    "row_number": row_num,
                    **preamble,
                },
            ))

    return chunks

def _detect_header(rows: list[list[str]]) -> int:
    """
    扫描前 _HEADER_SCAN_ROWS 行，返回非空列数最多的行索引。
    """
    scan = rows[:_HEADER_SCAN_ROWS]
    best_idx = -1
    best_count = 0

    for i, row in enumerate(scan):
        non_empty = sum(1 for cell in row if cell.strip())
        if non_empty >= 2 and best_idx == -1:
            best_idx = i
            best_count = non_empty
        elif non_empty > best_count + 1:
            best_idx = i
            best_count = non_empty

    return best_idx

def _extract_preamble(rows: list[list[str]], header_idx: int) -> dict[str, str]:
    """
    提取表头之前的信息行，解析为 key: value 字典写入 metadata
    """
    preamble: dict[str, str] = {}
    for i, row in enumerate(rows[:header_idx]):
        non_empty = [cell.strip() for cell in row if cell.strip()]
        if not non_empty:
            continue
        
        if len(non_empty) >= 2:
            for j in range(0, len(non_empty) - 1, 2):
                preamble[non_empty[j]] = non_empty[j + 1]

        else:
            text = non_empty[0]
            for sep in (":", "："):
                if sep in text:
                    k, _, v = text.partition(sep)
                    preamble[k.strip()] = v.strip()
                    break
            else:
                preamble[f"row_{i}"] = text
    return preamble

def _row_to_text(headers: list[str], row: list[str]) -> str:
    """将一行数据转为 `字段: 值; 字段: 值` 格式"""
    pairs = []
    for header, value in zip(headers, row):
        v = value.strip()
        if v:
            pairs.append(f"{header}: {v}")

    for i, value in enumerate(row[len(headers):], start=len(headers)):
        v = value.strip()
        if v:
            pairs.append(f"col_{i}: {v}")
    return "; ".join(pairs)