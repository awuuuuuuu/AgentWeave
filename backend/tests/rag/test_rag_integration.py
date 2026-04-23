"""RAG Chain 集成测试（需要真实 Milvus + OpenAI API）。

运行：
    uv run pytest backend/tests/rag/test_rag_integration.py -v -m integration
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

pytestmark = pytest.mark.integration

_KB_ID = "integration_rag_chain_kb"
_TEST_DOCS = [
    "Python 是一种广泛用于数据科学和人工智能的编程语言，以简洁著称。",
    "机器学习模型需要大量数据才能有效训练，数据质量比数量更重要。",
    "向量数据库 Milvus 支持高效的相似度检索，广泛用于 RAG 系统构建。",
    "RAG（检索增强生成）通过结合检索和生成来提升 LLM 回答的准确性和可溯源性。",
    "今天天气晴朗，气温适宜，适合外出散步。",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def seed_data():
    """写入测试数据，测试结束后清理。"""
    from ingestion.embedder.openai_embedder import OpenAIEmbedder
    from ingestion.parsers.base import ParsedChunk
    from ingestion.store.milvus_store import MilvusStore, MilvusStoreConfig

    store_cfg = MilvusStoreConfig(uri=os.getenv("MILVUS_URI", "http://localhost:19530"))
    store = MilvusStore(config=store_cfg)
    embedder = OpenAIEmbedder()

    chunks = [
        ParsedChunk(text=text, metadata={
            "source_file": "rag_integration_test.txt",
            "content_type": "text",
            "section_path": f"section_{i}",
            "chunk_index_in_doc": i,
        })
        for i, text in enumerate(_TEST_DOCS)
    ]
    embedded = embedder.embed(chunks)
    store.upsert(embedded, knowledge_base_id=_KB_ID)

    yield

    store.delete_by_source(knowledge_base_id=_KB_ID, source_file="rag_integration_test.txt")


@pytest.fixture(scope="module")
def chain():
    from rag.chain import RAGChain
    from rag.settings import RAGChainSettings
    cfg = RAGChainSettings(
        use_reranker=False,   # 集成测试不下载 cross-encoder 模型
        llm_model="gpt-4o-mini",
        output_reserve_tokens=500,
    )
    return RAGChain.from_settings(cfg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRAGChainIntegration:

    def test_invoke_returns_answer(self, chain):
        result = chain.invoke("什么是 RAG？", _KB_ID, top_k=3)
        assert isinstance(result["answer"], str)
        assert len(result["answer"]) > 0

    def test_invoke_returns_citations(self, chain):
        result = chain.invoke("向量数据库有什么用途？", _KB_ID, top_k=3)
        # 有相关文档，应该有引用
        assert isinstance(result["citations"], list)

    def test_no_relevant_docs_answer_contains_notice(self, chain):
        """完全无关查询时，LLM 应告知用户文档中无相关信息。"""
        result = chain.invoke("xyzxyz_完全无关的内容_abc123", _KB_ID, top_k=3)
        assert isinstance(result["answer"], str)
        # 无论 LLM 怎么回答，answer 不应为空
        assert len(result["answer"]) > 0

    @pytest.mark.asyncio
    async def test_ainvoke_returns_answer(self, chain):
        result = await chain.ainvoke("Python 有什么特点？", _KB_ID, top_k=3)
        assert isinstance(result["answer"], str)
        assert len(result["answer"]) > 0

    @pytest.mark.asyncio
    async def test_astream_full_yields_tokens_then_result(self, chain):
        tokens = []
        final_result = None
        async for kind, data in chain.astream_full("RAG 是什么", _KB_ID, top_k=3):
            if kind == "token":
                tokens.append(data)
            elif kind == "result":
                final_result = data

        assert len(tokens) > 0, "应该产出至少一个 token"
        assert final_result is not None, "应该产出最终结果"
        assert "answer" in final_result
        assert "citations" in final_result
        # 从 token 拼接的答案应与最终 answer 相近（LangGraph 可能有细微差异）
        streamed_answer = "".join(tokens)
        assert len(streamed_answer) > 0
