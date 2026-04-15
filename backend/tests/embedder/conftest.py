"""
Embedder 测试共享工具
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.parsers.base import ParsedChunk


def make_chunk(text: str, content_type: str = "text", **meta) -> ParsedChunk:
    base = {"content_type": content_type, "source_file": "test.txt", "section_path": ""}
    return ParsedChunk(text=text, metadata={**base, **meta})


def make_mock_client(vectors: list[list[float]] | None = None) -> MagicMock:
    """构造返回固定向量的 mock OpenAI client。"""
    client = MagicMock()

    def fake_create(model, input, **kwargs):
        vecs = vectors if vectors is not None else [[0.1, 0.2, 0.3]] * len(input)
        response = MagicMock()
        response.data = [
            _make_embedding_obj(i, v) for i, v in enumerate(vecs)
        ]
        return response

    client.embeddings.create.side_effect = fake_create
    return client


def _make_embedding_obj(index: int, vector: list[float]) -> MagicMock:
    obj = MagicMock()
    obj.index = index
    obj.embedding = vector
    return obj
