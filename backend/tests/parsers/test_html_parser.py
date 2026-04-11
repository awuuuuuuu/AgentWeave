"""
HtmlParser 测试套件

运行：
  uv run pytest backend/tests/parsers/test_html_parser.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.parsers.base import ParsedChunk, get_parser
from ingestion.parsers.html_parser import HtmlParser, _build_section_path, _clean_text, _decode


# ── 工具 ────────────────────────────────────────────────────────────────────────

def _parse(html: str, **kwargs) -> list[ParsedChunk]:
    return HtmlParser().parse(html.encode("utf-8"), **kwargs)


# ── 基础结构测试 ────────────────────────────────────────────────────────────────

class TestHtmlParserBasic:

    def test_registered_for_html(self):
        assert isinstance(get_parser(".html"), HtmlParser)

    def test_registered_for_htm(self):
        assert isinstance(get_parser(".htm"), HtmlParser)

    def test_returns_list(self):
        result = _parse("<html><body><p>Hello</p></body></html>")
        assert isinstance(result, list)

    def test_returns_parsed_chunks(self):
        result = _parse("<html><body><p>Hello</p></body></html>")
        assert len(result) > 0
        assert all(isinstance(c, ParsedChunk) for c in result)

    def test_chunk_has_required_metadata(self):
        chunks = _parse("<html><body><h1>Title</h1><p>Body.</p></body></html>")
        for chunk in chunks:
            assert "source_file" in chunk.metadata
            assert "content_type" in chunk.metadata
            assert "section_path" in chunk.metadata

    def test_empty_body_returns_empty(self):
        result = _parse("<html><body></body></html>")
        assert result == []

    def test_parse_from_path(self, tmp_path):
        path = tmp_path / "test.html"
        path.write_bytes(b"<html><body><p>Hello</p></body></html>")
        chunks = HtmlParser().parse(path)
        assert len(chunks) > 0

    def test_source_file_from_bytes_is_unknown(self):
        chunks = _parse("<p>text</p>")
        assert all(c.metadata["source_file"] == "unknown" for c in chunks)

    def test_source_file_from_path(self, tmp_path):
        path = tmp_path / "page.html"
        path.write_bytes(b"<p>text</p>")
        chunks = HtmlParser().parse(path)
        assert all(c.metadata["source_file"] == "page.html" for c in chunks)


# ── 标题测试 ────────────────────────────────────────────────────────────────────

class TestHeadings:

    def test_h1_is_title(self):
        chunks = _parse("<h1>Main Title</h1><p>Body.</p>")
        h1 = next(c for c in chunks if c.text == "Main Title")
        assert h1.metadata["content_type"] == "title"
        assert h1.metadata["heading_level"] == 1

    def test_h2_to_h6_are_titles(self):
        html = "".join(f"<h{i}>Heading {i}</h{i}>" for i in range(2, 7))
        chunks = _parse(html)
        for i in range(2, 7):
            chunk = next(c for c in chunks if c.text == f"Heading {i}")
            assert chunk.metadata["heading_level"] == i

    def test_body_is_text(self):
        chunks = _parse("<h1>Title</h1><p>Body paragraph.</p>")
        body = next(c for c in chunks if c.text == "Body paragraph.")
        assert body.metadata["content_type"] == "text"

    def test_heading_level_in_metadata(self):
        chunks = _parse("<h3>Section</h3>")
        h3 = next(c for c in chunks if c.text == "Section")
        assert h3.metadata["heading_level"] == 3


# ── section_path 测试 ───────────────────────────────────────────────────────────

class TestSectionPath:

    def test_body_under_h1_has_path(self):
        chunks = _parse("<h1>Chapter</h1><p>Content.</p>")
        body = next(c for c in chunks if c.text == "Content.")
        assert "Chapter" in body.metadata["section_path"]

    def test_nested_headings(self):
        chunks = _parse("<h1>Chapter 1</h1><h2>Section 1.1</h2><p>Body.</p>")
        body = next(c for c in chunks if c.text == "Body.")
        path = body.metadata["section_path"]
        assert "Chapter 1" in path
        assert "Section 1.1" in path

    def test_h2_clears_on_new_h1(self):
        chunks = _parse("<h1>Ch1</h1><h2>Sec1</h2><h1>Ch2</h1><p>After.</p>")
        after = next(c for c in chunks if c.text == "After.")
        path = after.metadata["section_path"]
        assert "Ch2" in path
        assert "Sec1" not in path

    def test_heading_itself_in_path(self):
        chunks = _parse("<h1>Chapter</h1>")
        h1 = next(c for c in chunks if c.text == "Chapter")
        assert "Chapter" in h1.metadata["section_path"]

    def test_build_section_path_direct(self):
        assert _build_section_path({}) == ""
        assert _build_section_path({1: "A"}) == "A"
        assert _build_section_path({1: "A", 2: "B"}) == "A > B"
        assert _build_section_path({1: "A", 3: "C"}) == "A > C"  # 跳级


# ── Noise Removal 测试 ─────────────────────────────────────────────────────────

class TestNoiseRemoval:

    def test_nav_is_removed(self):
        chunks = _parse("<nav><a>Home</a><a>About</a></nav><p>Content.</p>")
        texts = [c.text for c in chunks]
        assert "Home" not in " ".join(texts)
        assert "Content." in texts

    def test_script_is_removed(self):
        chunks = _parse("<script>alert('xss')</script><p>Safe.</p>")
        texts = [c.text for c in chunks]
        assert "alert" not in " ".join(texts)

    def test_style_is_removed(self):
        chunks = _parse("<style>body{color:red}</style><p>Text.</p>")
        texts = [c.text for c in chunks]
        assert "color" not in " ".join(texts)

    def test_aside_is_removed(self):
        chunks = _parse("<aside>Ad content</aside><p>Main text.</p>")
        texts = [c.text for c in chunks]
        assert "Ad content" not in " ".join(texts)

    def test_html_comments_removed(self):
        chunks = _parse("<!-- secret comment --><p>Visible.</p>")
        texts = [c.text for c in chunks]
        assert "secret" not in " ".join(texts)



# ── 表格测试 ────────────────────────────────────────────────────────────────────

class TestTableParsing:

    def test_table_content_type(self):
        html = "<table><tr><td>A</td><td>B</td></tr></table>"
        chunks = _parse(html)
        assert any(c.metadata["content_type"] == "table" for c in chunks)

    def test_table_text_is_html(self):
        html = "<table><tr><td>Widget</td><td>100</td></tr></table>"
        chunks = _parse(html)
        table = next(c for c in chunks if c.metadata["content_type"] == "table")
        assert "<table>" in table.text
        assert "<td>" in table.text

    def test_table_has_plain_text_metadata(self):
        html = "<table><tr><td>Product</td><td>Price</td></tr></table>"
        chunks = _parse(html)
        table = next(c for c in chunks if c.metadata["content_type"] == "table")
        assert "plain_text" in table.metadata
        assert "Product" in table.metadata["plain_text"]

    def test_table_section_path(self):
        html = "<h1>Data</h1><table><tr><td>val</td></tr></table>"
        chunks = _parse(html)
        table = next(c for c in chunks if c.metadata["content_type"] == "table")
        assert "Data" in table.metadata["section_path"]


# ── 编码检测测试 ────────────────────────────────────────────────────────────────

class TestEncoding:

    def test_utf8_decoded_correctly(self):
        html = "<p>中文内容</p>".encode("utf-8")
        chunks = HtmlParser().parse(html)
        assert any("中文内容" in c.text for c in chunks)

    def test_gbk_decoded_correctly(self):
        html = "<p>中文内容</p>".encode("gbk")
        chunks = HtmlParser().parse(html)
        assert any("中文内容" in c.text for c in chunks)

    def test_decode_fallback_latin1(self):
        # latin-1 字节流，chardet 可能检测为 latin-1
        raw = b"<p>caf\xe9</p>"  # café in latin-1
        chunks = HtmlParser().parse(raw)
        assert len(chunks) > 0  # 不崩溃

    def test_decode_utf8_directly(self):
        result = _decode("<p>Hello</p>".encode("utf-8"))
        assert "Hello" in result


# ── 空白清理测试 ────────────────────────────────────────────────────────────────

class TestWhitespaceCleaning:

    def test_nbsp_normalized(self):
        html = "<p>Hello&nbsp;World</p>"
        chunks = _parse(html)
        body = next(c for c in chunks if c.metadata["content_type"] == "text")
        assert "\xa0" not in body.text

    def test_no_double_spaces(self):
        html = "<p>Too   many   spaces</p>"
        chunks = _parse(html)
        for chunk in chunks:
            assert "  " not in chunk.text

    def test_clean_text_direct(self):
        assert _clean_text("Hello\xa0World") == "Hello World"
        assert _clean_text("  too   many  spaces  ") == "too many spaces"
        assert _clean_text("\n\t\r mixed \n") == "mixed"


# ── 输出顺序测试 ────────────────────────────────────────────────────────────────

class TestOutputOrder:

    def test_heading_before_body(self):
        chunks = _parse("<h1>Title</h1><p>Body.</p>")
        texts = [c.text for c in chunks]
        assert texts.index("Title") < texts.index("Body.")

    def test_document_order_preserved(self):
        chunks = _parse("<p>First.</p><p>Second.</p><p>Third.</p>")
        texts = [c.text for c in chunks]
        assert texts.index("First.") < texts.index("Second.")
        assert texts.index("Second.") < texts.index("Third.")


# ── metadata schema 一致性测试 ──────────────────────────────────────────────────

class TestMetadataSchema:

    _TEXT_KEYS = {"source_file", "content_type", "section_path", "tag"}
    _TITLE_KEYS = {"source_file", "content_type", "section_path", "heading_level", "tag"}
    _TABLE_KEYS = {"source_file", "content_type", "section_path", "plain_text"}

    def test_text_schema(self):
        chunks = _parse("<h1>H</h1><p>Body.</p>")
        for c in chunks:
            if c.metadata["content_type"] == "text":
                assert self._TEXT_KEYS <= c.metadata.keys()

    def test_title_schema(self):
        chunks = _parse("<h1>Title</h1>")
        for c in chunks:
            if c.metadata["content_type"] == "title":
                assert self._TITLE_KEYS <= c.metadata.keys()

    def test_table_schema(self):
        chunks = _parse("<table><tr><td>v</td></tr></table>")
        for c in chunks:
            if c.metadata["content_type"] == "table":
                assert self._TABLE_KEYS <= c.metadata.keys()

    def test_content_type_values_valid(self):
        html = "<h1>T</h1><p>B</p><table><tr><td>v</td></tr></table>"
        chunks = _parse(html)
        valid = {"text", "title", "table"}
        for c in chunks:
            assert c.metadata["content_type"] in valid


# ── 容错测试 ────────────────────────────────────────────────────────────────────

class TestErrorHandling:

    def test_malformed_html_no_exception(self):
        result = _parse("<p>unclosed <b>tag")
        assert isinstance(result, list)

    def test_corrupted_bytes_no_exception(self):
        result = HtmlParser().parse(b"\xff\xfe invalid bytes \x00\x01")
        assert isinstance(result, list)

    def test_corrupted_bytes_returns_error_or_chunks(self):
        # 要么成功解析出 chunk，要么返回 error chunk，不崩溃
        result = HtmlParser().parse(b"not html at all !!!")
        assert isinstance(result, list)
