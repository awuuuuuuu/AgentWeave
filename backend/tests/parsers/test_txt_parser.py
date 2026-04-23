"""
PlainTextParser 测试套件

运行：
  uv run pytest backend/tests/parsers/test_txt_parser.py -v
"""
from __future__ import annotations

from pathlib import Path


from ingestion.parsers.base import ParsedChunk, get_parser
from ingestion.parsers.txt_parser import PlainTextParser


# ── 工具 ────────────────────────────────────────────────────────────────────────

def _parse(text: str) -> list[ParsedChunk]:
    return PlainTextParser().parse(text.encode("utf-8"))


# ── 基础结构 ────────────────────────────────────────────────────────────────────

class TestPlainTextParserBasic:

    def test_registered_for_txt(self):
        assert isinstance(get_parser(".txt"), PlainTextParser)

    def test_returns_list(self):
        assert isinstance(_parse("Hello world"), list)

    def test_non_empty_text_returns_chunk(self):
        assert len(_parse("Hello world")) == 1

    def test_empty_text_returns_empty(self):
        assert _parse("") == []
        assert _parse("   \n\n   ") == []

    def test_chunk_has_required_metadata(self):
        chunks = _parse("Some text here.")
        assert "source_file" in chunks[0].metadata
        assert "content_type" in chunks[0].metadata
        assert "section_path" in chunks[0].metadata

    def test_content_type_is_text(self):
        chunks = _parse("Hello.")
        assert all(c.metadata["content_type"] == "text" for c in chunks)

    def test_section_path_is_empty(self):
        chunks = _parse("No structure here.")
        assert all(c.metadata["section_path"] == "" for c in chunks)

    def test_source_file_from_bytes_is_unknown(self):
        chunks = _parse("text")
        assert chunks[0].metadata["source_file"] == "unknown"

    def test_source_file_from_path(self, tmp_path):
        p = tmp_path / "doc.txt"
        p.write_text("content", encoding="utf-8")
        chunks = PlainTextParser().parse(p)
        assert chunks[0].metadata["source_file"] == "doc.txt"


# ── 切块逻辑 ────────────────────────────────────────────────────────────────────

class TestPlainTextSplitting:

    def test_split_by_blank_line(self):
        chunks = _parse("First paragraph.\n\nSecond paragraph.")
        assert len(chunks) == 2

    def test_multiple_blank_lines_count_as_one(self):
        chunks = _parse("First.\n\n\n\nSecond.")
        assert len(chunks) == 2

    def test_single_paragraph_no_split(self):
        chunks = _parse("Line one.\nLine two.\nLine three.")
        assert len(chunks) == 1

    def test_newlines_preserved_within_paragraph(self):
        chunks = _parse("Line one.\nLine two.")
        assert "\n" in chunks[0].text

    def test_trailing_whitespace_stripped_per_line(self):
        chunks = _parse("Hello   \nWorld   ")
        assert not any(line.endswith("   ") for line in chunks[0].text.splitlines())

    def test_gbk_encoding(self):
        raw = "第一段落。\n\n第二段落。".encode("gbk")
        chunks = PlainTextParser().parse(raw)
        assert len(chunks) == 2
        assert "第一段落" in chunks[0].text
