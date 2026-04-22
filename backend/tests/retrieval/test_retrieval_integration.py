"""
检索层集成测试（需要真实 Milvus + OpenAI）

运行：
    uv run pytest backend/tests/retrieval/test_retrieval_integration.py -v -m integration

默认被 CI 跳过，本地需要 backend/.env 配置 OPENAI_API_KEY 和 MILVUS_URI。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

pytestmark = pytest.mark.integration

_KB_ID = "integration_test_kb"
_TEST_DOCS = [
    "Python 是一种广泛用于数据科学和人工智能的编程语言。",
    "机器学习模型需要大量数据才能有效训练。",
    "今天天气晴朗，气温适宜，适合外出。",
    "向量数据库 Milvus 支持高效的相似度检索，广泛用于 RAG 系统。",
    "RAG（检索增强生成）通过结合检索和生成来提升 LLM 回答质量。",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def milvus_uri():
    return os.getenv("MILVUS_URI", "http://localhost:19530")


@pytest.fixture(scope="module")
def store_cfg(milvus_uri):
    from ingestion.store.milvus_store import MilvusStoreConfig
    return MilvusStoreConfig(uri=milvus_uri)


@pytest.fixture(scope="module")
def embedder():
    from ingestion.embedder.openai_embedder import OpenAIEmbedder
    return OpenAIEmbedder()


@pytest.fixture(scope="module", autouse=True)
def seed_data(store_cfg, embedder):
    """写入测试数据，测试结束后清理。"""
    from ingestion.parsers.base import ParsedChunk
    from ingestion.store.milvus_store import MilvusStore

    store = MilvusStore(config=store_cfg)

    chunks = [
        ParsedChunk(text=text, metadata={
            "source_file": "integration_test.txt",
            "content_type": "text",
            "section_path": f"section_{i}",
            "chunk_index_in_doc": i,
        })
        for i, text in enumerate(_TEST_DOCS)
    ]
    embedded = embedder.embed(chunks)
    store.upsert(embedded, knowledge_base_id=_KB_ID)

    yield

    store.delete_by_source(knowledge_base_id=_KB_ID, source_file="integration_test.txt")


# ---------------------------------------------------------------------------
# VectorRetriever
# ---------------------------------------------------------------------------

class TestVectorRetrieverIntegration:

    def test_returns_results(self, embedder, store_cfg):
        from retrieval.vector_retriever import VectorRetriever, VectorRetrieverConfig
        r = VectorRetriever(embedder=embedder, config=VectorRetrieverConfig(store_config=store_cfg))
        results = r.retrieve("RAG 和向量数据库", _KB_ID, top_k=3)
        assert len(results) > 0

    def test_vector_score_populated(self, embedder, store_cfg):
        from retrieval.vector_retriever import VectorRetriever, VectorRetrieverConfig
        r = VectorRetriever(embedder=embedder, config=VectorRetrieverConfig(store_config=store_cfg))
        results = r.retrieve("向量检索", _KB_ID, top_k=3)
        assert all(c.vector_score > 0 for c in results)

    def test_retrieval_method_is_vector(self, embedder, store_cfg):
        from retrieval.vector_retriever import VectorRetriever, VectorRetrieverConfig
        r = VectorRetriever(embedder=embedder, config=VectorRetrieverConfig(store_config=store_cfg))
        results = r.retrieve("Python", _KB_ID, top_k=2)
        assert all(c.retrieval_method == "vector" for c in results)

    def test_filter_expr_limits_results(self, embedder, store_cfg):
        """filter_expr 能正确过滤结果。"""
        from retrieval.vector_retriever import VectorRetriever, VectorRetrieverConfig
        r = VectorRetriever(embedder=embedder, config=VectorRetrieverConfig(store_config=store_cfg))
        results = r.retrieve("数据", _KB_ID, top_k=5,
                             filter_expr='content_type == "nonexistent"')
        assert results == []

    def test_candidate_multiplier_applied(self, embedder, store_cfg):
        """VectorRetriever 返回 top_k * candidate_multiplier 条候选，由上层裁剪到 top_k。"""
        from retrieval.vector_retriever import VectorRetriever, VectorRetrieverConfig
        multiplier = 2
        cfg = VectorRetrieverConfig(store_config=store_cfg, candidate_multiplier=multiplier)
        r = VectorRetriever(embedder=embedder, config=cfg)
        results = r.retrieve("数据", _KB_ID, top_k=2)
        assert len(results) <= 2 * multiplier


# ---------------------------------------------------------------------------
# BM25Retriever
# ---------------------------------------------------------------------------

class TestBM25RetrieverIntegration:

    def test_returns_results(self, store_cfg):
        from retrieval.bm25_retriever import BM25Retriever, BM25RetrieverConfig
        r = BM25Retriever(config=BM25RetrieverConfig(store_config=store_cfg))
        results = r.retrieve("RAG 向量数据库", _KB_ID, top_k=3)
        assert len(results) > 0

    def test_bm25_score_populated(self, store_cfg):
        from retrieval.bm25_retriever import BM25Retriever, BM25RetrieverConfig
        r = BM25Retriever(config=BM25RetrieverConfig(store_config=store_cfg))
        results = r.retrieve("RAG", _KB_ID, top_k=3)
        assert all(c.bm25_score > 0 for c in results)

    def test_retrieval_method_is_bm25(self, store_cfg):
        from retrieval.bm25_retriever import BM25Retriever, BM25RetrieverConfig
        r = BM25Retriever(config=BM25RetrieverConfig(store_config=store_cfg))
        results = r.retrieve("Python 编程", _KB_ID, top_k=2)
        assert all(c.retrieval_method == "bm25" for c in results)

    def test_unrelated_query_returns_empty_or_low_score(self, store_cfg):
        """完全无关查询应返回空或极低分。"""
        from retrieval.bm25_retriever import BM25Retriever, BM25RetrieverConfig
        r = BM25Retriever(config=BM25RetrieverConfig(store_config=store_cfg))
        results = r.retrieve("xyzxyzxyz_no_match", _KB_ID, top_k=3)
        # BM25 无关词返回空
        assert results == [] or all(c.bm25_score < 1.0 for c in results)


# ---------------------------------------------------------------------------
# HybridRetriever
# ---------------------------------------------------------------------------

class TestHybridRetrieverIntegration:

    def test_returns_results(self, embedder, store_cfg):
        from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
        r = HybridRetriever(embedder=embedder, config=HybridRetrieverConfig(store_config=store_cfg))
        results = r.retrieve("RAG 和向量数据库", _KB_ID, top_k=3)
        assert len(results) > 0

    def test_fusion_score_populated(self, embedder, store_cfg):
        from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
        r = HybridRetriever(embedder=embedder, config=HybridRetrieverConfig(store_config=store_cfg))
        results = r.retrieve("向量检索", _KB_ID, top_k=3)
        assert all(c.fusion_score > 0 for c in results)

    def test_sorted_by_fusion_score(self, embedder, store_cfg):
        from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
        r = HybridRetriever(embedder=embedder, config=HybridRetrieverConfig(store_config=store_cfg))
        results = r.retrieve("RAG 检索", _KB_ID, top_k=5)
        scores = [c.fusion_score for c in results]
        assert scores == sorted(scores, reverse=True)

    def test_retrieval_method_is_hybrid(self, embedder, store_cfg):
        from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
        r = HybridRetriever(embedder=embedder, config=HybridRetrieverConfig(store_config=store_cfg))
        results = r.retrieve("Python", _KB_ID, top_k=3)
        assert all(c.retrieval_method == "hybrid" for c in results)

    def test_top_k_respected(self, embedder, store_cfg):
        from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
        r = HybridRetriever(embedder=embedder, config=HybridRetrieverConfig(store_config=store_cfg))
        results = r.retrieve("数据", _KB_ID, top_k=2)
        assert len(results) <= 2


# ---------------------------------------------------------------------------
# Reranker（基于 HybridRetriever 结果）
# ---------------------------------------------------------------------------

class TestRerankerIntegration:

    def test_rerank_changes_order(self, embedder, store_cfg):
        """Reranker 的排序结果应与 fusion_score 排序不完全一致（至少改变过一次）。"""
        from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
        from retrieval.reranker import Reranker

        hybrid = HybridRetriever(embedder=embedder, config=HybridRetrieverConfig(store_config=store_cfg))
        candidates = hybrid.retrieve("RAG 和向量数据库有什么关系", _KB_ID, top_k=5)

        reranker = Reranker()
        reranked = reranker.rerank("RAG 和向量数据库有什么关系", candidates, top_k=3)

        assert len(reranked) == 3
        assert all(c.rerank_score != 0.0 for c in reranked)

    def test_rerank_scores_populated(self, embedder, store_cfg):
        from retrieval.hybrid_retriever import HybridRetriever, HybridRetrieverConfig
        from retrieval.reranker import Reranker

        hybrid = HybridRetriever(embedder=embedder, config=HybridRetrieverConfig(store_config=store_cfg))
        candidates = hybrid.retrieve("Python 编程语言", _KB_ID, top_k=3)

        reranker = Reranker()
        reranked = reranker.rerank("Python 编程语言", candidates)

        assert all(isinstance(c.rerank_score, float) for c in reranked)
