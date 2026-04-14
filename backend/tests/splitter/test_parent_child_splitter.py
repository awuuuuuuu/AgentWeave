"""
ParentChildSplitter 测试

运行：
  uv run pytest backend/tests/splitter/test_parent_child_splitter.py -v
"""
from __future__ import annotations

from ingestion.splitter import ParentChildConfig, ParentChildSplitter

from .conftest import make_chunk


def _pc(parent_size: int = 50, child_size: int = 15, child_overlap: int = 3) -> ParentChildSplitter:
    return ParentChildSplitter(ParentChildConfig(
        parent_chunk_size=parent_size,
        child_chunk_size=child_size,
        child_overlap=child_overlap,
    ))


def _long_text(words: int = 300) -> str:
    return " ".join([f"word{i}" for i in range(words)])


# ── 基础行为 ────────────────────────────────────────────────────────────────────

class TestBasicBehavior:

    def test_short_text_returns_one_child(self):
        """文本短于 child_chunk_size 时只产生一个子块。"""
        results = _pc().split([make_chunk("Short text.")])
        assert len(results) == 1

    def test_long_text_produces_multiple_children(self):
        results = _pc().split([make_chunk(_long_text(300))])
        assert len(results) > 1

    def test_table_not_split(self):
        results = _pc().split([make_chunk(_long_text(300), content_type="table")])
        assert len(results) == 1


# ── 父子关系 ────────────────────────────────────────────────────────────────────

class TestParentChildRelation:

    def test_each_child_has_parent_text(self):
        results = _pc().split([make_chunk(_long_text(300))])
        for c in results:
            assert "parent_text" in c.metadata
            assert len(c.metadata["parent_text"]) > 0

    def test_each_child_has_parent_index(self):
        results = _pc().split([make_chunk(_long_text(300))])
        for c in results:
            assert "parent_index" in c.metadata
            assert isinstance(c.metadata["parent_index"], int)

    def test_parent_index_monotone(self):
        """parent_index 应单调非递减。"""
        results = _pc().split([make_chunk(_long_text(300))])
        indices = [c.metadata["parent_index"] for c in results]
        assert indices == sorted(indices)

    def test_child_text_comes_from_parent(self):
        """每个子块的首词应在其父块文本中出现。"""
        results = _pc().split([make_chunk(_long_text(300))])
        for c in results:
            first_word = c.text.split()[0] if c.text.split() else ""
            assert first_word in c.metadata["parent_text"], (
                f"子块首词 '{first_word}' 不在父块中"
            )


# ── Metadata ────────────────────────────────────────────────────────────────────

class TestMetadata:

    def test_metadata_inherited(self):
        chunk = make_chunk(_long_text(300), section_path="Ch2", source_file="doc.pdf")
        results = _pc().split([chunk])
        for c in results:
            assert c.metadata["section_path"] == "Ch2"
            assert c.metadata["source_file"] == "doc.pdf"

    def test_chunk_index_present(self):
        """每个子块均携带 chunk_index（在其父块内的位置）。"""
        results = _pc().split([make_chunk(_long_text(300))])
        for c in results:
            assert "chunk_index" in c.metadata
            assert isinstance(c.metadata["chunk_index"], int)


# ── 配置 ────────────────────────────────────────────────────────────────────────

class TestConfig:

    def test_default_config(self):
        pc = ParentChildSplitter()
        assert pc.config.parent_chunk_size == 512
        assert pc.config.child_chunk_size == 128
        assert pc.config.child_overlap == 16
