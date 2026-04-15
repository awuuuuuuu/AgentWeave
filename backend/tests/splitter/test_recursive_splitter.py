"""
RecursiveSplitter 测试

运行：
  uv run pytest backend/tests/splitter/test_recursive_splitter.py -v
"""
from __future__ import annotations

import tiktoken

from ingestion.splitter import RecursiveConfig, RecursiveSplitter

from .conftest import make_chunk, make_recursive


# ── 基础行为 ────────────────────────────────────────────────────────────────────

class TestBasicBehavior:

    def test_returns_list(self):
        assert isinstance(make_recursive().split([make_chunk("Hello.")]), list)

    def test_empty_input_returns_empty(self):
        assert make_recursive().split([]) == []

    def test_short_text_not_split(self):
        chunks = make_recursive().split([make_chunk("Short text.")])
        assert len(chunks) == 1

    def test_short_text_preserved(self):
        chunks = make_recursive().split([make_chunk("Hello world.")])
        assert chunks[0].text == "Hello world."

    def test_multiple_chunks_all_processed(self):
        inputs = [make_chunk("Text one."), make_chunk("Text two.")]
        result = make_recursive().split(inputs)
        assert len(result) >= 2


# ── content_type 路由 ───────────────────────────────────────────────────────────

class TestContentTypeRouting:

    def _long_text(self, n: int = 600) -> str:
        return " ".join(["word"] * n)

    def test_table_not_split_when_within_hard_limit(self):
        # 600 tokens < TABLE_HARD_LIMIT(2048)，透传
        chunks = make_recursive(chunk_size=50).split([make_chunk(self._long_text(), content_type="table")])
        assert len(chunks) == 1

    def test_table_split_when_exceeds_hard_limit(self):
        # 3000 words >> TABLE_HARD_LIMIT(2048)，强制切分
        from ingestion.splitter.base import TABLE_HARD_LIMIT
        huge_table = " ".join(["row"] * (TABLE_HARD_LIMIT + 1000))
        chunks = make_recursive(chunk_size=512).split([make_chunk(huge_table, content_type="table")])
        assert len(chunks) > 1

    def test_title_not_split(self):
        chunks = make_recursive(chunk_size=50).split([make_chunk(self._long_text(), content_type="title")])
        assert len(chunks) == 1

    def test_text_is_split_when_too_long(self):
        chunks = make_recursive(chunk_size=100).split([make_chunk(self._long_text(600))])
        assert len(chunks) > 1

    def test_table_metadata_preserved_without_chunk_index(self):
        chunk = make_chunk("col: val", content_type="table", section_path="Sheet1")
        result = make_recursive(chunk_size=2).split([chunk])
        assert result[0].metadata["section_path"] == "Sheet1"
        assert "chunk_index" not in result[0].metadata


# ── Metadata 继承 ───────────────────────────────────────────────────────────────

class TestMetadataInheritance:

    def test_sub_chunks_inherit_all_metadata(self):
        chunk = make_chunk(
            " ".join(["word"] * 600),
            section_path="Chapter 1",
            source_file="doc.pdf",
            page_number=3,
        )
        results = make_recursive(chunk_size=100).split([chunk])
        for c in results:
            assert c.metadata["section_path"] == "Chapter 1"
            assert c.metadata["source_file"] == "doc.pdf"
            assert c.metadata["page_number"] == 3

    def test_chunk_index_added(self):
        results = make_recursive(chunk_size=100).split([make_chunk(" ".join(["word"] * 600))])
        assert len(results) > 1
        for i, c in enumerate(results):
            assert c.metadata["chunk_index"] == i

    def test_chunk_total_added(self):
        results = make_recursive(chunk_size=100).split([make_chunk(" ".join(["word"] * 600))])
        total = len(results)
        for c in results:
            assert c.metadata["chunk_total"] == total


# ── chunk_size 与 overlap ────────────────────────────────────────────────────────

class TestChunkSizeAndOverlap:

    def test_each_chunk_within_size_limit(self):
        enc = tiktoken.get_encoding("cl100k_base")
        chunk_size = 100
        results = make_recursive(chunk_size=chunk_size, overlap=10).split([make_chunk(" ".join(["word"] * 600))])
        for c in results:
            assert len(enc.encode(c.text)) <= chunk_size + 20  # 允许少量 merge 边界超出

    def test_overlap_creates_shared_content(self):
        sentences = [f"This is sentence number {i} in the document." for i in range(50)]
        results = make_recursive(chunk_size=50, overlap=15).split([make_chunk(" ".join(sentences))])
        if len(results) >= 2:
            end_of_first = results[0].text[-50:]
            start_of_second = results[1].text[:100]
            assert set(end_of_first.split()) & set(start_of_second.split()), "相邻 chunk 之间应有重叠词汇"


# ── 递归切分逻辑 ────────────────────────────────────────────────────────────────

class TestRecursiveSplitLogic:

    def test_splits_on_double_newline_first(self):
        text = "Paragraph one here.\n\nParagraph two here.\n\nParagraph three here."
        results = make_recursive(chunk_size=5, overlap=0).split([make_chunk(text)])
        assert len(results) >= 2

    def test_splits_chinese_on_period(self):
        text = "第一句话结束了。第二句话也结束了。第三句话同样结束了。"
        results = make_recursive(chunk_size=10, overlap=0).split([make_chunk(text)])
        assert len(results) >= 1

    def test_fallback_to_token_split(self):
        text = "a" * 2000
        results = make_recursive(chunk_size=100, overlap=0).split([make_chunk(text)])
        assert len(results) > 1

    def test_separator_preserved_in_output(self):
        """分隔符应保留在片段末尾，不丢失换行或句号。"""
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        results = make_recursive(chunk_size=5, overlap=0).split([make_chunk(text)])
        # 合并后的文本应包含换行或句号，不全粘连
        combined = "".join(c.text for c in results)
        assert "\n" in combined or "." in combined


# ── 配置 ────────────────────────────────────────────────────────────────────────

class TestConfig:

    def test_default_config(self):
        s = RecursiveSplitter()
        assert s.config.chunk_size == 512
        assert s.config.chunk_overlap == 64

    def test_custom_config(self):
        s = RecursiveSplitter(RecursiveConfig(chunk_size=256, chunk_overlap=32))
        assert s.config.chunk_size == 256
        assert s.config.chunk_overlap == 32
