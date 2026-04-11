"""
MarkdownParser 测试套件

运行：
  uv run pytest backend/tests/parsers/test_markdown_parser.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.parsers.base import ParsedChunk, get_parser
from ingestion.parsers.markdown_parser import (
    MarkdownParser,
    _build_section_path,
    _split_frontmatter,
)


# ── 工具 ────────────────────────────────────────────────────────────────────────

def _parse(text: str) -> list[ParsedChunk]:
    return MarkdownParser().parse(text.encode("utf-8"))


# ── 基础结构 ────────────────────────────────────────────────────────────────────

class TestMarkdownParserBasic:

    def test_registered_for_md(self):
        assert isinstance(get_parser(".md"), MarkdownParser)

    def test_registered_for_markdown(self):
        assert isinstance(get_parser(".markdown"), MarkdownParser)

    def test_returns_list(self):
        assert isinstance(_parse("# Hello"), list)

    def test_empty_returns_empty(self):
        assert _parse("") == []

    def test_chunk_has_required_metadata(self):
        for c in _parse("# Title\n\nBody."):
            assert "source_file" in c.metadata
            assert "content_type" in c.metadata
            assert "section_path" in c.metadata


# ── 标题解析 ────────────────────────────────────────────────────────────────────

class TestMarkdownHeadings:

    def test_h1_is_title(self):
        chunks = _parse("# Main Title\n\nBody.")
        title = next(c for c in chunks if c.text == "Main Title")
        assert title.metadata["content_type"] == "title"
        assert title.metadata["heading_level"] == 1

    def test_h2_to_h6_are_titles(self):
        md = "\n".join(f"{'#' * i} Heading {i}" for i in range(2, 7))
        chunks = _parse(md)
        for i in range(2, 7):
            chunk = next(c for c in chunks if c.text == f"Heading {i}")
            assert chunk.metadata["heading_level"] == i

    def test_body_is_text(self):
        chunks = _parse("# Title\n\nBody paragraph.")
        body = next(c for c in chunks if "Body paragraph" in c.text)
        assert body.metadata["content_type"] == "text"

    def test_heading_text_stripped(self):
        chunks = _parse("#   Spaced Title   \n\nBody.")
        title = next(c for c in chunks if c.metadata["content_type"] == "title")
        assert title.text == "Spaced Title"


# ── section_path ────────────────────────────────────────────────────────────────

class TestMarkdownSectionPath:

    def test_body_under_h1_has_path(self):
        chunks = _parse("# Chapter\n\nContent.")
        body = next(c for c in chunks if "Content" in c.text)
        assert "Chapter" in body.metadata["section_path"]

    def test_nested_headings_build_path(self):
        chunks = _parse("# Ch1\n## Sec1.1\n\nBody.")
        body = next(c for c in chunks if "Body" in c.text)
        path = body.metadata["section_path"]
        assert "Ch1" in path
        assert "Sec1.1" in path

    def test_new_h1_resets_h2(self):
        chunks = _parse("# Ch1\n## Sec1\n# Ch2\n\nAfter.")
        after = next(c for c in chunks if "After" in c.text)
        path = after.metadata["section_path"]
        assert "Ch2" in path
        assert "Sec1" not in path

    def test_text_before_any_heading_has_empty_path(self):
        chunks = _parse("Preamble text.\n\n# Title")
        preamble = next((c for c in chunks if "Preamble" in c.text), None)
        if preamble:
            assert preamble.metadata["section_path"] == ""

    def test_build_section_path_direct(self):
        assert _build_section_path({}) == ""
        assert _build_section_path({1: "A"}) == "A"
        assert _build_section_path({1: "A", 2: "B"}) == "A > B"
        assert _build_section_path({1: "A", 3: "C"}) == "A > C"


# ── 代码块状态机 ─────────────────────────────────────────────────────────────────

class TestMarkdownCodeBlocks:

    def test_hash_in_code_block_not_heading(self):
        md = "# Real Heading\n\n```python\n# not a heading\ncode()\n```\n\nAfter."
        chunks = _parse(md)
        titles = [c for c in chunks if c.metadata["content_type"] == "title"]
        assert len(titles) == 1
        assert titles[0].text == "Real Heading"

    def test_code_block_content_preserved(self):
        md = "# Title\n\n```python\ndef foo():\n    return 1\n```"
        chunks = _parse(md)
        assert any("def foo" in c.text for c in chunks)

    def test_tilde_code_block_not_heading(self):
        md = "~~~\n# not a heading\n~~~\n\nAfter."
        chunks = _parse(md)
        assert not any(c.metadata["content_type"] == "title" for c in chunks)

    def test_section_path_not_corrupted_by_code_comment(self):
        md = "# Chapter\n\n```\n# code comment\n```\n\n## Section\n\nBody."
        chunks = _parse(md)
        body = next(c for c in chunks if "Body" in c.text)
        path = body.metadata["section_path"]
        assert "Chapter" in path
        assert "Section" in path
        assert "code comment" not in path


# ── 换行保留 ────────────────────────────────────────────────────────────────────

class TestMarkdownNewlinePreservation:

    def test_list_items_keep_newlines(self):
        md = "# Title\n\n- Apple\n- Banana\n- Cherry"
        chunks = _parse(md)
        body = next(c for c in chunks if "Apple" in c.text)
        assert "\n" in body.text

    def test_multiline_paragraph_keeps_newlines(self):
        chunks = _parse("Line one.\nLine two.\nLine three.")
        assert "\n" in chunks[0].text


# ── Frontmatter ──────────────────────────────────────────────────────────────────

class TestMarkdownFrontmatter:

    def test_frontmatter_extracted_to_metadata(self):
        md = "---\ntitle: My Doc\nauthor: Alice\n---\n\n# Heading\n\nBody."
        for c in _parse(md):
            assert c.metadata.get("title") == "My Doc"
            assert c.metadata.get("author") == "Alice"

    def test_frontmatter_not_in_text(self):
        md = "---\ntitle: Hidden\n---\n\n# Title\n\nBody."
        texts = " ".join(c.text for c in _parse(md))
        assert "Hidden" not in texts

    def test_no_frontmatter_works_normally(self):
        chunks = _parse("# Title\n\nBody.")
        assert any(c.text == "Title" for c in chunks)

    def test_frontmatter_eof_no_trailing_newline(self):
        chunks = MarkdownParser().parse(b"---\ntitle: X\n---")
        assert isinstance(chunks, list)

    def test_split_frontmatter_direct(self):
        fm, body = _split_frontmatter("---\nkey: value\nauthor: Bob\n---\n\nBody here.")
        assert fm["key"] == "value"
        assert fm["author"] == "Bob"
        assert "Body here" in body

    def test_split_frontmatter_no_frontmatter(self):
        fm, body = _split_frontmatter("Just regular content.")
        assert fm == {}
        assert "regular" in body

    def test_frontmatter_quoted_values_stripped(self):
        chunks = _parse('---\ntitle: "Quoted Title"\n---\n\n# H')
        assert chunks[0].metadata.get("title") == "Quoted Title"
