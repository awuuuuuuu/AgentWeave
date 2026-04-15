"""
OpenAIEmbedder 集成测试（需要真实 API Key）

运行：
  uv run pytest backend/tests/embedder/test_openai_embedder_integration.py -v -s

CI 中跳过：pytest 默认不收集此文件（文件名含 integration）
或手动标记跳过：pytest -m "not integration"
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from openai import OpenAI

from ingestion.embedder import EmbeddedChunk, OpenAIEmbedder, OpenAIEmbedderConfig

from .conftest import make_chunk

# 加载 backend/.env
load_dotenv(Path(__file__).parent.parent.parent / ".env")

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def embedder() -> OpenAIEmbedder:
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")

    if not api_key:
        pytest.skip("未设置 OPENAI_API_KEY，跳过集成测试")

    client = OpenAI(api_key=api_key, base_url=base_url or None)
    return OpenAIEmbedder(
        config=OpenAIEmbedderConfig(model="text-embedding-3-small"),
        client=client,
    )


class TestRealAPI:

    def test_single_chunk_returns_vector(self, embedder):
        chunks = [make_chunk("这是一段用于测试的文本。")]
        results = embedder.embed(chunks)

        assert len(results) == 1
        result = results[0]
        assert isinstance(result, EmbeddedChunk)
        assert len(result.embedding) == 1536     # text-embedding-3-small 维度
        assert result.embed_model == "text-embedding-3-small"
        assert not result.skipped

    def test_multiple_chunks_correct_count(self, embedder):
        chunks = [make_chunk(f"第 {i} 段测试文本，内容各不相同。") for i in range(5)]
        results = embedder.embed(chunks)

        assert len(results) == 5
        for r in results:
            assert len(r.embedding) == 1536

    def test_vectors_are_different_for_different_texts(self, embedder):
        chunks = [
            make_chunk("苹果是一种水果。"),
            make_chunk("量子力学是物理学的一个分支。"),
        ]
        results = embedder.embed(chunks)
        assert results[0].embedding != results[1].embedding

    def test_error_chunk_skipped(self, embedder):
        chunks = [
            make_chunk("正常文本"),
            make_chunk("", content_type="error"),
            make_chunk("另一段正常文本"),
        ]
        results = embedder.embed(chunks)

        assert len(results) == 3
        assert not results[0].skipped
        assert results[1].skipped
        assert not results[2].skipped

    def test_chinese_and_english_mixed(self, embedder):
        chunks = [make_chunk("RAGent is an enterprise RAG system. 企业级检索增强生成系统。")]
        results = embedder.embed(chunks)
        assert len(results[0].embedding) == 1536
