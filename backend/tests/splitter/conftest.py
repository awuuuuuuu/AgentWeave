"""
Splitter 测试共享工具

所有 test_*.py 通过 pytest conftest 自动获取，无需手动导入。
"""
from __future__ import annotations

from pathlib import Path

import pytest


from ingestion.parsers.base import ParsedChunk
from ingestion.splitter import RecursiveConfig, RecursiveSplitter


def make_chunk(text: str, content_type: str = "text", **meta) -> ParsedChunk:
    base = {"content_type": content_type, "source_file": "test.txt", "section_path": ""}
    return ParsedChunk(text=text, metadata={**base, **meta})


def make_recursive(chunk_size: int = 512, overlap: int = 64) -> RecursiveSplitter:
    return RecursiveSplitter(RecursiveConfig(chunk_size=chunk_size, chunk_overlap=overlap))


# ── fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture
def chunk():
    """返回工厂函数，供测试按需构造 ParsedChunk。"""
    return make_chunk


@pytest.fixture
def splitter():
    """返回工厂函数，供测试按需构造 RecursiveSplitter。"""
    return make_recursive
