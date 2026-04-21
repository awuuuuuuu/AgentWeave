from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.embedder.base import EmbeddedChunk
from ingestion.parsers.base import ParsedChunk


def make_chunk(text: str = "hello", content_type: str = "text") -> ParsedChunk:
    return ParsedChunk(
        text=text,
        metadata={"source_file": "test.txt", "content_type": content_type, "section_path": ""},
    )


def make_embedded(text: str = "hello", content_type: str = "text") -> EmbeddedChunk:
    return EmbeddedChunk(
        chunk=make_chunk(text, content_type),
        embedding=[0.1] * 1536,
        embed_model="text-embedding-3-small",
    )


def make_mock_embedder(chunks_out: list[EmbeddedChunk] | None = None) -> MagicMock:
    embedder = MagicMock()
    embedder.embed.return_value = chunks_out or [make_embedded()]
    return embedder


def make_mock_store() -> MagicMock:
    store = MagicMock()
    store.upsert.return_value = 1
    return store


def make_mock_splitter(chunks_out: list[ParsedChunk] | None = None) -> MagicMock:
    splitter = MagicMock()
    splitter.split.return_value = chunks_out or [make_chunk()]
    return splitter
