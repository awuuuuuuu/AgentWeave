from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


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
    """返回一个模拟 MilvusClient，has_collection 默认返回 True（跳过建表）。

    describe_collection 返回包含 sparse_vector 的 schema，使 _needs_migration() 返回 False，
    避免触发 schema 过期报错。
    """
    client = MagicMock()
    client.has_collection.return_value = True
    client.describe_collection.return_value = {
        "fields": [
            # 包含 _REQUIRED_FIELDS 中的全部字段，使 _needs_migration() 返回 False
            {"name": "chunk_id"},
            {"name": "knowledge_base_id"},
            {"name": "source_file"},
            {"name": "content_type"},
            {"name": "section_path"},
            {"name": "embed_model"},
            {"name": "chunk_index_in_doc"},
            {"name": "text"},
            {"name": "extra_meta"},
            {"name": "vector"},
            {"name": "sparse_vector"},
        ]
    }
    return client
