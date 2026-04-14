"""
SemanticSplitter 测试

运行：
  uv run pytest backend/tests/splitter/test_semantic_splitter.py -v
"""
from __future__ import annotations

from ingestion.splitter import SemanticConfig, SemanticSplitter

from .conftest import make_chunk


def _fake_embed(sentences: list[str]) -> list[list[float]]:
    """奇数索引返回正交向量，模拟话题切换（相似度=0）。"""
    return [[1.0, 0.0] if i % 2 == 0 else [0.0, 1.0] for i, _ in enumerate(sentences)]


def _same_embed(sentences: list[str]) -> list[list[float]]:
    """所有句子返回相同向量，相似度=1，不切分。"""
    return [[1.0, 0.0]] * len(sentences)


def _sem(embed_fn=None, chunk_size: int = 512) -> SemanticSplitter:
    return SemanticSplitter(embed_fn or _fake_embed, SemanticConfig(chunk_size=chunk_size))


# ── 短路逻辑 ────────────────────────────────────────────────────────────────────

class TestShortCircuit:

    def test_short_text_not_split(self):
        """token 数 ≤ chunk_size 时直接返回，不调用 embed_fn。"""
        called = []

        def embed_fn(sents):
            called.append(sents)
            return [[1.0, 0.0]] * len(sents)

        results = SemanticSplitter(embed_fn, SemanticConfig(chunk_size=512)).split([make_chunk("Short.")])
        assert len(results) == 1
        assert not called, "短文本不应调用 embed_fn"

    def test_table_not_split(self):
        text = "。".join([f"句子{i}" for i in range(20)])
        results = _sem().split([make_chunk(text, content_type="table")])
        assert len(results) == 1


# ── 相似度触发切分 ──────────────────────────────────────────────────────────────

class TestSimilarityBehavior:

    def test_high_similarity_no_split(self):
        """相似度=1 时不按语义切分（超出 chunk_size 会硬切兜底）。"""
        text = " ".join([f"Sentence {i} is here." for i in range(20)])
        results = _sem(embed_fn=_same_embed, chunk_size=10).split([make_chunk(text)])
        assert len(results) >= 1

    def test_low_similarity_triggers_split(self):
        """相邻句子正交（相似度=0）且超出 chunk_size，应切分。"""
        text = "。".join([f"Sentence {i}." for i in range(20)])
        results = _sem(embed_fn=_fake_embed, chunk_size=20).split([make_chunk(text)])
        assert len(results) > 1


# ── 容错 ────────────────────────────────────────────────────────────────────────

class TestFallback:

    def test_embed_fn_failure_returns_original(self):
        """embed_fn 抛出异常时降级返回原 chunk，不中断 pipeline。"""
        def bad_embed(sents):
            raise RuntimeError("API error")

        text = "。".join([f"句子{i}" for i in range(20)])
        results = SemanticSplitter(bad_embed, SemanticConfig(chunk_size=10)).split([make_chunk(text)])
        assert len(results) == 1


# ── Metadata ────────────────────────────────────────────────────────────────────

class TestMetadata:

    def test_metadata_inherited(self):
        text = "。".join([f"Sentence {i}." for i in range(20)])
        chunk = make_chunk(text, section_path="Ch1", page_number=5)
        results = _sem(embed_fn=_fake_embed, chunk_size=512).split([chunk])
        for c in results:
            assert c.metadata["section_path"] == "Ch1"
            assert c.metadata["page_number"] == 5


# ── 配置 ────────────────────────────────────────────────────────────────────────

class TestConfig:

    def test_default_config(self):
        s = SemanticSplitter(lambda x: [[1.0]] * len(x))
        assert s.config.chunk_size == 512
        assert s.config.breakpoint_threshold == 0.7
