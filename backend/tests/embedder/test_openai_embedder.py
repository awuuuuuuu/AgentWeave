"""
OpenAIEmbedder 测试

运行：
  uv run pytest backend/tests/embedder/ -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch
import time

import openai
import pytest

from ingestion.embedder import EmbeddedChunk, OpenAIEmbedder, OpenAIEmbedderConfig

from .conftest import make_chunk, make_mock_client


def _embedder(client=None, batch_size: int = 512, max_retries: int = 3) -> OpenAIEmbedder:
    cfg = OpenAIEmbedderConfig(batch_size=batch_size, max_retries=max_retries)
    return OpenAIEmbedder(config=cfg, client=client or make_mock_client())


# ── 基础行为 ────────────────────────────────────────────────────────────────────

class TestBasicBehavior:

    def test_empty_input_returns_empty(self):
        assert _embedder().embed([]) == []

    def test_returns_same_length_as_input(self):
        chunks = [make_chunk(f"text {i}") for i in range(5)]
        results = _embedder().embed(chunks)
        assert len(results) == 5

    def test_output_is_embedded_chunk(self):
        result = _embedder().embed([make_chunk("hello")])[0]
        assert isinstance(result, EmbeddedChunk)

    def test_original_chunk_preserved(self):
        chunk = make_chunk("hello", section_path="Ch1", page_number=3)
        result = _embedder().embed([chunk])[0]
        assert result.chunk is chunk
        assert result.chunk.metadata["section_path"] == "Ch1"
        assert result.chunk.metadata["page_number"] == 3

    def test_embedding_vector_set(self):
        client = make_mock_client(vectors=[[0.1, 0.2, 0.3]])
        result = _embedder(client).embed([make_chunk("hello")])[0]
        assert result.embedding == [0.1, 0.2, 0.3]

    def test_embed_model_set(self):
        result = _embedder().embed([make_chunk("hello")])[0]
        assert result.embed_model == "text-embedding-3-small"

    def test_order_preserved(self):
        """输出顺序与输入一致。"""
        vecs = [[float(i)] * 3 for i in range(5)]
        client = make_mock_client(vectors=vecs)
        chunks = [make_chunk(f"text {i}") for i in range(5)]
        results = _embedder(client).embed(chunks)
        for i, r in enumerate(results):
            assert r.embedding == [float(i)] * 3


# ── error chunk 跳过 ────────────────────────────────────────────────────────────

class TestErrorChunkSkip:

    def test_error_chunk_skipped(self):
        chunk = make_chunk("", content_type="error")
        result = _embedder().embed([chunk])[0]
        assert result.skipped
        assert result.embedding == []
        assert result.embed_model == ""

    def test_error_chunk_not_sent_to_api(self):
        client = make_mock_client()
        chunk = make_chunk("", content_type="error")
        _embedder(client).embed([chunk])
        client.embeddings.create.assert_not_called()

    def test_mixed_chunks_order_preserved(self):
        """error chunk 夹在中间，其余 chunk 向量正确对应。"""
        vecs = [[1.0, 0.0], [0.0, 1.0]]
        client = make_mock_client(vectors=vecs)
        chunks = [
            make_chunk("text A"),
            make_chunk("", content_type="error"),
            make_chunk("text B"),
        ]
        results = _embedder(client).embed(chunks)
        assert len(results) == 3
        assert results[0].embedding == [1.0, 0.0]
        assert results[1].skipped
        assert results[2].embedding == [0.0, 1.0]


# ── 批处理 ──────────────────────────────────────────────────────────────────────

class TestBatching:

    def test_large_input_batched(self):
        """超过 batch_size 时应拆成多批 API 调用。"""
        client = make_mock_client()
        chunks = [make_chunk(f"text {i}") for i in range(10)]
        _embedder(client, batch_size=3).embed(chunks)
        # 10 条 / batch_size=3 → 至少 4 次调用
        assert client.embeddings.create.call_count >= 4

    def test_small_input_single_batch(self):
        client = make_mock_client()
        chunks = [make_chunk(f"text {i}") for i in range(5)]
        _embedder(client, batch_size=512).embed(chunks)
        assert client.embeddings.create.call_count == 1

    def test_result_count_matches_input(self):
        chunks = [make_chunk(f"text {i}") for i in range(20)]
        results = _embedder(batch_size=7).embed(chunks)
        assert len(results) == 20


# ── token 截断 ──────────────────────────────────────────────────────────────────

class TestTokenTruncation:

    def test_long_text_truncated_before_api_call(self):
        """超过模型 token 上限的文本应被截断，API 仍能正常调用。"""
        client = make_mock_client()
        cfg = OpenAIEmbedderConfig(model="text-embedding-3-small")
        embedder = OpenAIEmbedder(config=cfg, client=client)

        # 构造超长文本（约 9000 tokens）
        long_text = "word " * 9000
        chunks = [make_chunk(long_text)]
        results = embedder.embed(chunks)

        assert len(results) == 1
        assert not results[0].skipped
        # 验证实际发送的文本已被截断
        sent_text = client.embeddings.create.call_args[1]["input"][0]
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        assert len(enc.encode(sent_text)) <= 8191


# ── 重试 ────────────────────────────────────────────────────────────────────────

class TestRetry:

    def test_rate_limit_retried(self):
        """429 RateLimitError 应触发重试。"""
        client = MagicMock()
        success_response = MagicMock()
        success_response.data = [_make_emb(0, [0.1, 0.2])]

        client.embeddings.create.side_effect = [
            openai.RateLimitError("rate limit", response=MagicMock(), body={}),
            success_response,
        ]

        cfg = OpenAIEmbedderConfig(max_retries=3, retry_base_delay=0.0)
        embedder = OpenAIEmbedder(config=cfg, client=client)
        results = embedder.embed([make_chunk("hello")])

        assert client.embeddings.create.call_count == 2
        assert results[0].embedding == [0.1, 0.2]

    def test_server_error_retried(self):
        """5xx 错误应触发重试。"""
        client = MagicMock()
        success_response = MagicMock()
        success_response.data = [_make_emb(0, [0.5])]

        server_err = openai.APIStatusError(
            "server error",
            response=MagicMock(status_code=500),
            body={},
        )
        client.embeddings.create.side_effect = [server_err, success_response]

        cfg = OpenAIEmbedderConfig(max_retries=3, retry_base_delay=0.0)
        embedder = OpenAIEmbedder(config=cfg, client=client)
        results = embedder.embed([make_chunk("hello")])

        assert client.embeddings.create.call_count == 2

    def test_connection_error_retried(self):
        """网络连接异常（DNS / TCP）应触发重试。"""
        client = MagicMock()
        success_response = MagicMock()
        success_response.data = [_make_emb(0, [0.9])]

        client.embeddings.create.side_effect = [
            openai.APIConnectionError(request=MagicMock()),
            success_response,
        ]

        cfg = OpenAIEmbedderConfig(max_retries=3, retry_base_delay=0.0)
        embedder = OpenAIEmbedder(config=cfg, client=client)
        results = embedder.embed([make_chunk("hello")])

        assert client.embeddings.create.call_count == 2
        assert results[0].embedding == [0.9]

    def test_no_sleep_on_last_retry(self):
        """最后一次重试失败后不应再等待。"""
        client = MagicMock()
        client.embeddings.create.side_effect = openai.RateLimitError(
            "rate limit", response=MagicMock(), body={}
        )

        cfg = OpenAIEmbedderConfig(max_retries=2, retry_base_delay=10.0)
        embedder = OpenAIEmbedder(config=cfg, client=client)

        start = time.time()
        with pytest.raises(RuntimeError):
            embedder.embed([make_chunk("hello")])
        elapsed = time.time() - start

        # max_retries=2：第 1 次失败等 10s，第 2 次失败不等待
        # 实际耗时应 < 15s（若最后一次也睡则 >= 20s）
        assert elapsed < 15, f"最后一次重试后不应再 sleep，但耗时 {elapsed:.1f}s"

    def test_client_error_not_retried(self):
        """4xx 错误（非 429）不重试，直接抛出。"""
        client = MagicMock()
        client_err = openai.APIStatusError(
            "bad request",
            response=MagicMock(status_code=400),
            body={},
        )
        client.embeddings.create.side_effect = client_err

        cfg = OpenAIEmbedderConfig(max_retries=3, retry_base_delay=0.0)
        embedder = OpenAIEmbedder(config=cfg, client=client)

        with pytest.raises(openai.APIStatusError):
            embedder.embed([make_chunk("hello")])

        assert client.embeddings.create.call_count == 1

    def test_retry_exhausted_raises(self):
        """重试次数耗尽后抛出 RuntimeError。"""
        client = MagicMock()
        client.embeddings.create.side_effect = openai.RateLimitError(
            "rate limit", response=MagicMock(), body={}
        )

        cfg = OpenAIEmbedderConfig(max_retries=2, retry_base_delay=0.0)
        embedder = OpenAIEmbedder(config=cfg, client=client)

        with pytest.raises(RuntimeError, match="已重试"):
            embedder.embed([make_chunk("hello")])

        assert client.embeddings.create.call_count == 2


# ── 工具 ────────────────────────────────────────────────────────────────────────

def _make_emb(index: int, vector: list[float]) -> MagicMock:
    obj = MagicMock()
    obj.index = index
    obj.embedding = vector
    return obj
