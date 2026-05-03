"""retrieve_node 路由逻辑单元测试。

验证：
  1. retrieval_mode="vector"   → 只调 _vector.aretrieve，不调 _bm25
  2. retrieval_mode="fulltext" → 只调 _bm25.aretrieve，不调 _vector
  3. retrieval_mode="hybrid"   → 调 HybridRetriever.aretrieve（_vector + _bm25 两路）
  4. use_rerank=True  且 reranker 存在 → 调 reranker.rerank
  5. use_rerank=False 或 reranker=None → 不调 reranker
  6. score_threshold > 0 → 过滤低分 chunk
  7. score_threshold = 0 → 不过滤
  8. top_k 控制最终返回数量

全部 mock，不依赖 Milvus / DB / 网络。

运行：
    uv run pytest backend/tests/rag/test_retrieve_node.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from retrieval.base import RetrievedChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chunk(chunk_id: str, fusion_score: float = 0.8, vector_score: float = 0.8) -> RetrievedChunk:
    c = RetrievedChunk(
        chunk_id=chunk_id,
        text=f"text of {chunk_id}",
        source_file="doc.md",
        section_path="",
        chunk_index_in_doc=0,
        vector_score=vector_score,
        retrieval_method="hybrid",
    )
    c.fusion_score = fusion_score
    return c


def _make_retriever(chunks: list[RetrievedChunk]) -> MagicMock:
    """构造 mock HybridRetriever，_vector 和 _bm25 子检索器都返回 chunks。"""
    retriever = MagicMock()
    retriever.aretrieve = AsyncMock(return_value=chunks)
    retriever._vector = MagicMock()
    retriever._vector.aretrieve = AsyncMock(return_value=[
        _make_chunk(c.chunk_id, c.fusion_score, c.vector_score) for c in chunks
    ])
    retriever._bm25 = MagicMock()
    retriever._bm25.aretrieve = AsyncMock(return_value=[
        _make_chunk(c.chunk_id, c.fusion_score) for c in chunks
    ])
    return retriever


def _make_reranker(return_chunks: list[RetrievedChunk]) -> MagicMock:
    reranker = MagicMock()
    reranker.rerank = MagicMock(return_value=return_chunks)
    return reranker


def _make_state(
    retrieval_mode: str = "hybrid",
    use_rerank: bool = False,
    score_threshold: float = 0.0,
    top_k: int = 5,
) -> dict:
    return {
        "query": "什么是 RAG",
        "knowledge_base_id": "kb-test",
        "top_k": top_k,
        "retrieval_mode": retrieval_mode,
        "use_rerank": use_rerank,
        "score_threshold": score_threshold,
    }


# ---------------------------------------------------------------------------
# Tests: routing by retrieval_mode
# ---------------------------------------------------------------------------

class TestRetrievalModeRouting:

    @pytest.mark.asyncio
    async def test_vector_mode_calls_only_vector_retriever(self):
        chunks = [_make_chunk("c1"), _make_chunk("c2")]
        retriever = _make_retriever(chunks)
        reranker = None

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker)
        result = await node(_make_state(retrieval_mode="vector"))

        retriever._vector.aretrieve.assert_called_once()
        retriever._bm25.aretrieve.assert_not_called()
        retriever.aretrieve.assert_not_called()
        assert len(result["chunks"]) == len(chunks)

    @pytest.mark.asyncio
    async def test_fulltext_mode_calls_only_bm25_retriever(self):
        chunks = [_make_chunk("c1"), _make_chunk("c2")]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(retrieval_mode="fulltext"))

        retriever._bm25.aretrieve.assert_called_once()
        retriever._vector.aretrieve.assert_not_called()
        retriever.aretrieve.assert_not_called()
        assert len(result["chunks"]) == len(chunks)

    @pytest.mark.asyncio
    async def test_hybrid_mode_calls_hybrid_retriever(self):
        chunks = [_make_chunk("c1"), _make_chunk("c2"), _make_chunk("c3")]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(retrieval_mode="hybrid"))

        retriever.aretrieve.assert_called_once()
        retriever._vector.aretrieve.assert_not_called()
        retriever._bm25.aretrieve.assert_not_called()
        assert len(result["chunks"]) == len(chunks)

    @pytest.mark.asyncio
    async def test_unknown_mode_falls_back_to_hybrid(self):
        """未知 mode 默认走 hybrid 分支。"""
        chunks = [_make_chunk("c1")]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(retrieval_mode="unknown_mode"))

        retriever.aretrieve.assert_called_once()
        assert len(result["chunks"]) >= 0


# ---------------------------------------------------------------------------
# Tests: reranker
# ---------------------------------------------------------------------------

class TestReranker:

    @pytest.mark.asyncio
    async def test_reranker_called_when_use_rerank_true(self):
        chunks = [_make_chunk(f"c{i}") for i in range(5)]
        retriever = _make_retriever(chunks)
        reranked = chunks[:3]
        reranker = _make_reranker(reranked)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker)
        result = await node(_make_state(use_rerank=True, top_k=3))

        reranker.rerank.assert_called_once()
        assert result["chunks"] == reranked

    @pytest.mark.asyncio
    async def test_reranker_not_called_when_use_rerank_false(self):
        chunks = [_make_chunk(f"c{i}") for i in range(5)]
        retriever = _make_retriever(chunks)
        reranker = _make_reranker(chunks[:3])

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker)
        result = await node(_make_state(use_rerank=False, top_k=3))

        reranker.rerank.assert_not_called()
        assert len(result["chunks"]) == 3   # sliced to top_k

    @pytest.mark.asyncio
    async def test_reranker_not_called_when_none(self):
        """reranker=None 时即使 use_rerank=True 也不会崩溃。"""
        chunks = [_make_chunk(f"c{i}") for i in range(4)]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(use_rerank=True, top_k=4))

        assert len(result["chunks"]) == 4


# ---------------------------------------------------------------------------
# Tests: score_threshold filtering
# ---------------------------------------------------------------------------

class TestScoreThreshold:

    @pytest.mark.asyncio
    async def test_zero_threshold_returns_all(self):
        chunks = [
            _make_chunk("c1", fusion_score=0.1),
            _make_chunk("c2", fusion_score=0.5),
            _make_chunk("c3", fusion_score=0.9),
        ]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(score_threshold=0.0, top_k=10))

        assert len(result["chunks"]) == 3

    @pytest.mark.asyncio
    async def test_threshold_filters_low_score_chunks(self):
        chunks = [
            _make_chunk("c1", fusion_score=0.1),   # should be filtered
            _make_chunk("c2", fusion_score=0.3),   # should be filtered
            _make_chunk("c3", fusion_score=0.6),   # pass
            _make_chunk("c4", fusion_score=0.9),   # pass
        ]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(score_threshold=0.5, top_k=10))

        returned_ids = {c.chunk_id for c in result["chunks"]}
        assert "c1" not in returned_ids
        assert "c2" not in returned_ids
        assert "c3" in returned_ids
        assert "c4" in returned_ids

    @pytest.mark.asyncio
    async def test_threshold_above_all_scores_returns_empty(self):
        chunks = [_make_chunk(f"c{i}", fusion_score=0.2) for i in range(5)]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(score_threshold=0.9, top_k=10))

        assert result["chunks"] == []

    @pytest.mark.asyncio
    async def test_threshold_boundary_inclusive(self):
        """score_threshold 应为 >=（边界 chunk 应被保留）。"""
        chunks = [
            _make_chunk("exact", fusion_score=0.5),
            _make_chunk("below", fusion_score=0.49),
        ]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(score_threshold=0.5, top_k=10))

        ids = {c.chunk_id for c in result["chunks"]}
        assert "exact" in ids
        assert "below" not in ids


# ---------------------------------------------------------------------------
# Tests: top_k
# ---------------------------------------------------------------------------

class TestTopK:

    @pytest.mark.asyncio
    async def test_top_k_limits_returned_chunks(self):
        chunks = [_make_chunk(f"c{i}", fusion_score=1.0 - i * 0.1) for i in range(10)]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(top_k=3))

        assert len(result["chunks"]) == 3

    @pytest.mark.asyncio
    async def test_top_k_one_returns_single_chunk(self):
        chunks = [_make_chunk(f"c{i}") for i in range(5)]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(top_k=1))

        assert len(result["chunks"]) == 1

    @pytest.mark.asyncio
    async def test_top_k_larger_than_results_returns_all(self):
        chunks = [_make_chunk(f"c{i}") for i in range(3)]
        retriever = _make_retriever(chunks)

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker=None)
        result = await node(_make_state(top_k=100))

        assert len(result["chunks"]) == 3

    @pytest.mark.asyncio
    async def test_reranker_fetches_more_than_top_k(self):
        """使用 reranker 时，retrieve 阶段应取 rerank_fetch 条候选（>top_k）。"""
        chunks = [_make_chunk(f"c{i}") for i in range(20)]
        retriever = _make_retriever(chunks)
        reranker = _make_reranker(chunks[:5])

        from rag.nodes import make_retrieve_node
        node = make_retrieve_node(retriever, reranker, rerank_fetch=20)
        await node(_make_state(use_rerank=True, top_k=5, retrieval_mode="hybrid"))

        # hybrid retriever 被调用时 top_k 参数应是 rerank_fetch=20，不是 5
        call_kwargs = retriever.aretrieve.call_args
        assert call_kwargs.kwargs["top_k"] == 20
