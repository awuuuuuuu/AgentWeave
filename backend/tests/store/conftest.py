from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.embedder.base import EmbeddedChunk
from ingestion.parsers.base import ParsedChunk


def make_chunk(
    text: str = "hello",
    source_file: str = "test.pdf",
    content_type: str = "text",
    section_path: str = "",
    **extra_meta,
) -> ParsedChunk:
    meta = {
        "source_file": source_file,
        "content_type": content_type,
        "section_path": section_path,
        **extra_meta,
    }
    return ParsedChunk(text=text, metadata=meta)


def make_embedded(
    text: str = "hello",
    embedding: list[float] | None = None,
    embed_model: str = "text-embedding-3-small",
    content_type: str = "text",
    source_file: str = "test.pdf",
    section_path: str = "",
    **extra_meta,
) -> EmbeddedChunk:
    chunk = make_chunk(
        text=text,
        source_file=source_file,
        content_type=content_type,
        section_path=section_path,
        **extra_meta,
    )
    return EmbeddedChunk(
        chunk=chunk,
        embedding=embedding if embedding is not None else [0.1] * 1536,
        embed_model=embed_model,
    )


def make_mock_client() -> MagicMock:
    """返回一个模拟 MilvusClient，has_collection 默认返回 True（跳过建表）。"""
    client = MagicMock()
    client.has_collection.return_value = True
    return client
