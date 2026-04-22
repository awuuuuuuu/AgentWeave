"""检索层单元测试（全 mock，不依赖真实 Milvus）。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ingestion.store.milvus_store import MilvusStoreConfig
from retrieval.base import RetrievedChunk
from retrieval.bm25_retriever import BM25Retriever, BM25RetrieverConfig
from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig, _normalize
from retrieval.reranker import Reranker, RerankerConfig
from retrieval.vector_retriever import _kb_filter


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def _chunk(chunk_id: str, vector_score: float = 0.0, bm25_score: float = 0.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=f"text of {chunk_id}",
        source_file="doc.pdf",
        section_path="",
        chunk_index_in_doc=0,
        vector_score=vector_score,
        bm25_score=bm25_score,
    )


def _make_hybrid(vec_results: list, bm25_results: list, alpha: float = 0.7) -> HybridRetriever:
    """构造 mock HybridRetriever，注入预设的双路结果。

    patch MilvusClient 防止测试时真实连接 Milvus。
    """
    embedder = MagicMock()
    embedder.embed_query.return_value = [0.1] * 1536

    with patch("retrieval.vector_retriever.MilvusClient"), \
         patch("retrieval.bm25_retriever.MilvusClient"):
        retriever = HybridRetriever(
            embedder=embedder,
            config=HybridRetrieverConfig(alpha=alpha),
        )

    retriever._vector.retrieve = MagicMock(return_value=vec_results)
    retriever._bm25.retrieve = MagicMock(return_value=bm25_results)
    return retriever


# ---------------------------------------------------------------------------
# TestNormalize
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_basic(self):
        assert _normalize([0.0, 0.5, 1.0]) == [0.0, 0.5, 1.0]

    def test_all_same_returns_ones(self):
        assert _normalize([3.0, 3.0, 3.0]) == [1.0, 1.0, 1.0]

    def test_unbounded_bm25_scores(self):
        result = _normalize([10.0, 50.0, 90.0])
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.5)
        assert result[2] == pytest.approx(1.0)

    def test_empty(self):
        assert _normalize([]) == []

    def test_single(self):
        assert _normalize([42.0]) == [1.0]


# ---------------------------------------------------------------------------
# TestHybridRetriever
# ---------------------------------------------------------------------------

class TestHybridRetriever:
    def test_basic_fusion_returns_top_k(self):
        vec = [_chunk("a", vector_score=0.9), _chunk("b", vector_score=0.7)]
        bm25 = [_chunk("a", bm25_score=20.0), _chunk("c", bm25_score=15.0)]
        retriever = _make_hybrid(vec, bm25)

        results = retriever.retrieve("query", "kb1", top_k=2)

        assert len(results) == 2
        assert all(c.retrieval_method == "hybrid" for c in results)

    def test_fusion_score_assigned(self):
        vec  = [_chunk("a", vector_score=1.0)]
        bm25 = [_chunk("a", bm25_score=10.0)]
        retriever = _make_hybrid(vec, bm25, alpha=0.7)

        results = retriever.retrieve("query", "kb1", top_k=1)

        assert results[0].fusion_score == pytest.approx(0.7 * 1.0 + 0.3 * 1.0)

    def test_merges_both_paths(self):
        """两路各自独有的 chunk 都应出现在结果中。"""
        vec  = [_chunk("vec_only", vector_score=0.8)]
        bm25 = [_chunk("bm25_only", bm25_score=5.0)]
        retriever = _make_hybrid(vec, bm25)

        results = retriever.retrieve("query", "kb1", top_k=5)
        ids = {c.chunk_id for c in results}

        assert "vec_only" in ids
        assert "bm25_only" in ids

    def test_sorted_by_fusion_score_descending(self):
        vec  = [_chunk("low", vector_score=0.1), _chunk("high", vector_score=0.9)]
        bm25 = [_chunk("low", bm25_score=1.0),  _chunk("high", bm25_score=9.0)]
        retriever = _make_hybrid(vec, bm25)

        results = retriever.retrieve("query", "kb1", top_k=2)

        assert results[0].chunk_id == "high"
        assert results[1].chunk_id == "low"

    def test_zero_results_triggers_fallback(self):
        """双路均无结果时，触发 fallback 重试。"""
        retriever = _make_hybrid([], [])
        retriever._vector.retrieve = MagicMock(return_value=[])
        retriever._bm25.retrieve = MagicMock(return_value=[])

        results = retriever.retrieve("query", "kb1", top_k=5)

        assert results == []
        # fallback 会再触发一次双路检索
        assert retriever._vector.retrieve.call_count == 2
        assert retriever._bm25.retrieve.call_count == 2

    def test_bm25_score_merged_into_vec_chunk(self):
        """同一 chunk 同时出现在两路时，bm25_score 应合并到 vector_score 所在的 chunk。"""
        shared = _chunk("shared", vector_score=0.8)
        bm25_shared = _chunk("shared", bm25_score=30.0)
        retriever = _make_hybrid([shared], [bm25_shared])

        results = retriever.retrieve("query", "kb1", top_k=5)
        merged = next(c for c in results if c.chunk_id == "shared")

        assert merged.vector_score == pytest.approx(0.8)
        assert merged.bm25_score == pytest.approx(30.0)

    def test_alpha_weight_applied(self):
        """alpha=1.0 时，fusion_score 完全由 vector_score 决定。"""
        vec  = [_chunk("a", vector_score=0.9), _chunk("b", vector_score=0.1)]
        bm25 = [_chunk("a", bm25_score=100.0), _chunk("b", bm25_score=0.0)]
        retriever = _make_hybrid(vec, bm25, alpha=1.0)

        results = retriever.retrieve("query", "kb1", top_k=2)

        # alpha=1.0：bm25 权重为 0，排序完全由 vector_score 决定
        assert results[0].chunk_id == "a"


# ---------------------------------------------------------------------------
# TestReranker
# ---------------------------------------------------------------------------

class TestReranker:
    def _chunks(self, n: int = 3) -> list[RetrievedChunk]:
        return [_chunk(str(i), vector_score=float(i)) for i in range(n)]

    def test_rerank_assigns_scores(self):
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.3, 0.6]

        reranker = Reranker()
        reranker._model = mock_model

        results = reranker.rerank("query", self._chunks(3))

        assert all(c.rerank_score != 0.0 for c in results)

    def test_sorted_by_rerank_score(self):
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.9, 0.5]

        reranker = Reranker()
        reranker._model = mock_model
        chunks = self._chunks(3)

        results = reranker.rerank("query", chunks)

        assert results[0].rerank_score == pytest.approx(0.9)
        assert results[1].rerank_score == pytest.approx(0.5)

    def test_top_k_limits_output(self):
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.3, 0.6]

        reranker = Reranker()
        reranker._model = mock_model

        results = reranker.rerank("query", self._chunks(3), top_k=2)

        assert len(results) == 2

    def test_rerank_limit_caps_candidates(self):
        """超过 rerank_limit 的候选应被截断后再送入 cross-encoder。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = [float(i) for i in range(5)]

        reranker = Reranker(RerankerConfig(rerank_limit=5))
        reranker._model = mock_model
        chunks = self._chunks(10)  # 10 个候选

        reranker.rerank("query", chunks)

        pairs_sent = mock_model.predict.call_args[0][0]
        assert len(pairs_sent) == 5  # 只送了 5 个

    def test_empty_returns_empty(self):
        reranker = Reranker()
        assert reranker.rerank("query", []) == []

    def test_missing_sentence_transformers_raises(self):
        reranker = Reranker()
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            with pytest.raises(ImportError, match="sentence-transformers"):
                reranker._ensure_model()

    def test_top_k_zero_returns_empty(self):
        """top_k=0 应返回空列表，而不是全部候选。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.3, 0.6]
        reranker = Reranker()
        reranker._model = mock_model
        results = reranker.rerank("query", self._chunks(3), top_k=0)
        assert results == []

    def test_predict_length_mismatch_raises(self):
        """predict() 返回分数数量与候选数不一致时应抛出 RuntimeError。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.3]  # 只返回 2 个，但候选有 3 个
        reranker = Reranker()
        reranker._model = mock_model
        with pytest.raises(RuntimeError, match="模型输出异常"):
            reranker.rerank("query", self._chunks(3))


# ---------------------------------------------------------------------------
# TestBM25RetrieverConfig
# ---------------------------------------------------------------------------

class TestBM25RetrieverConfig:
    def test_raises_when_bm25_disabled(self):
        """enable_bm25=False 时 BM25Retriever 初始化应抛出 ValueError，给出明确错误信息。"""
        cfg = BM25RetrieverConfig(store_config=MilvusStoreConfig(enable_bm25=False))
        with patch("retrieval.bm25_retriever.MilvusClient"):
            with pytest.raises(ValueError, match="enable_bm25=True"):
                BM25Retriever(config=cfg)

    def test_succeeds_when_bm25_enabled(self):
        """enable_bm25=True（默认）时正常构造，不抛出。"""
        cfg = BM25RetrieverConfig(store_config=MilvusStoreConfig(enable_bm25=True))
        with patch("retrieval.bm25_retriever.MilvusClient"):
            retriever = BM25Retriever(config=cfg)
        assert retriever is not None


# ---------------------------------------------------------------------------
# TestEmbedQueryContract
# ---------------------------------------------------------------------------

class TestEmbedQueryContract:
    def test_embed_query_raises_on_empty_result(self):
        """_embed_texts 返回空列表时 embed_query 应抛出 ValueError。"""
        from ingestion.embedder.base import BaseEmbedder, EmbeddedChunk

        class EmptyEmbedder(BaseEmbedder):
            model_name = "test"
            def _embed_texts(self, texts):
                return [[]]  # 空向量

            def embed(self, chunks):
                return []

        embedder = EmptyEmbedder()
        with pytest.raises(ValueError, match="空向量"):
            embedder.embed_query("test query")

    def test_embed_query_returns_vector(self):
        """正常情况下 embed_query 返回非空向量。"""
        from ingestion.embedder.base import BaseEmbedder

        class DummyEmbedder(BaseEmbedder):
            model_name = "test"
            def _embed_texts(self, texts):
                return [[0.1, 0.2, 0.3]]

            def embed(self, chunks):
                return []

        embedder = DummyEmbedder()
        result = embedder.embed_query("hello")
        assert result == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# TestCandidateMultiplier — 候选倍数不被双重放大
# ---------------------------------------------------------------------------

class TestCandidateMultiplier:
    def test_sub_retrievers_called_with_correct_limit(self):
        """HybridRetriever 不应双重放大 top_k（子 retriever candidate_multiplier 固定为 1）。"""
        vec = [_chunk("a", vector_score=0.9)]
        bm25 = [_chunk("a", bm25_score=5.0)]
        retriever = _make_hybrid(vec, bm25)

        retriever.retrieve("query", "kb1", top_k=5)

        # multiplier=2（默认），子 retriever 的 candidate_multiplier 已固定为 1
        # 所以调用时传入的 top_k 应为 5 * 2 = 10，而非 5 * 2 * 2 = 20
        call_top_k = retriever._vector.retrieve.call_args[0][2]
        assert call_top_k == 10


# ---------------------------------------------------------------------------
# TestVectorRetrieverEf — ef 动态跟随 candidates
# ---------------------------------------------------------------------------

class TestVectorRetrieverEf:
    def _make_vector_retriever(self):
        from retrieval.vector_retriever import VectorRetriever, VectorRetrieverConfig
        embedder = MagicMock()
        embedder.embed_query.return_value = [0.1] * 1536
        mock_client = MagicMock()
        mock_client.search.return_value = [[]]
        with patch("retrieval.vector_retriever.MilvusClient", return_value=mock_client):
            r = VectorRetriever(embedder=embedder, config=VectorRetrieverConfig(candidate_multiplier=2))
        r._client = mock_client
        return r

    def test_ef_not_less_than_candidates(self):
        """candidates=100 时 ef 应 >= 100，不能用固定值 64。"""
        r = self._make_vector_retriever()
        r.retrieve("query", "kb1", top_k=50)  # candidates = 50 * 2 = 100... but multiplier=1 in sub_retriever
        # candidates = top_k * candidate_multiplier = 50 * 2 = 100
        search_params = r._client.search.call_args[1]["search_params"]
        ef = search_params["params"]["ef"]
        limit = r._client.search.call_args[1]["limit"]
        assert ef >= limit

    def test_ef_minimum_64_for_small_queries(self):
        """top_k 很小时 ef 保底为 64。"""
        r = self._make_vector_retriever()
        r.retrieve("query", "kb1", top_k=5)  # candidates = 5 * 2 = 10
        search_params = r._client.search.call_args[1]["search_params"]
        ef = search_params["params"]["ef"]
        assert ef >= 64


# ---------------------------------------------------------------------------
# TestKbFilter — knowledge_base_id 过滤表达式转义
# ---------------------------------------------------------------------------

class TestKbFilter:
    def test_normal_id(self):
        expr = _kb_filter("kb_123")
        assert expr == 'knowledge_base_id == "kb_123"'

    def test_id_with_double_quote_escaped(self):
        """含双引号的 kb_id 应被转义为 \" 而非裸 "，防止破坏表达式。"""
        expr = _kb_filter('kb"evil')
        # 转义后应包含 \" 序列
        assert '\\"' in expr

    def test_id_with_backslash_escaped(self):
        expr = _kb_filter("kb\\test")
        assert "\\\\" in expr
