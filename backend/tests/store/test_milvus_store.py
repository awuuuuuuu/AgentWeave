"""
MilvusStore 单元测试（全 mock，无需真实 Milvus）

运行：
  uv run pytest backend/tests/store/test_milvus_store.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from ingestion.store import MilvusStore, MilvusStoreConfig
from ingestion.store.milvus_store import _make_chunk_id

from .conftest import make_embedded, make_mock_client


def _store(client=None, batch_size: int = 200) -> MilvusStore:
    cfg = MilvusStoreConfig(batch_size=batch_size)
    return MilvusStore(config=cfg, client=client or make_mock_client())


# ── chunk_id 生成 ────────────────────────────────────────────────────────────

class TestChunkId:

    def test_deterministic(self):
        id1 = _make_chunk_id("kb1", "file.pdf", "hello world")
        id2 = _make_chunk_id("kb1", "file.pdf", "hello world")
        assert id1 == id2

    def test_different_kb_gives_different_id(self):
        id1 = _make_chunk_id("kb1", "file.pdf", "hello")
        id2 = _make_chunk_id("kb2", "file.pdf", "hello")
        assert id1 != id2

    def test_different_text_gives_different_id(self):
        id1 = _make_chunk_id("kb1", "file.pdf", "hello")
        id2 = _make_chunk_id("kb1", "file.pdf", "world")
        assert id1 != id2

    def test_different_source_gives_different_id(self):
        id1 = _make_chunk_id("kb1", "a.pdf", "hello")
        id2 = _make_chunk_id("kb1", "b.pdf", "hello")
        assert id1 != id2

    def test_length_is_32(self):
        cid = _make_chunk_id("kb", "f", "t")
        assert len(cid) == 32

    def test_hex_chars_only(self):
        cid = _make_chunk_id("kb", "f", "t")
        assert all(c in "0123456789abcdef" for c in cid)


# ── upsert 基础行为 ───────────────────────────────────────────────────────────

class TestUpsertBasic:

    def test_empty_input_returns_zero(self):
        store = _store()
        assert store.upsert([], knowledge_base_id="kb1") == 0

    def test_empty_input_no_api_call(self):
        client = make_mock_client()
        store = _store(client)
        store.upsert([], knowledge_base_id="kb1")
        client.upsert.assert_not_called()

    def test_returns_written_count(self):
        store = _store()
        chunks = [make_embedded(f"text {i}") for i in range(5)]
        count = store.upsert(chunks, knowledge_base_id="kb1")
        assert count == 5

    def test_single_chunk_calls_upsert_once(self):
        client = make_mock_client()
        store = _store(client)
        store.upsert([make_embedded("hello")], knowledge_base_id="kb1")
        assert client.upsert.call_count == 1

    def test_row_fields_complete(self):
        """写入行包含所有必要字段。"""
        client = make_mock_client()
        store = _store(client)
        ec = make_embedded(
            text="some text",
            source_file="doc.pdf",
            content_type="text",
            section_path="Ch1",
        )
        store.upsert([ec], knowledge_base_id="kb42")
        data = client.upsert.call_args[1]["data"]
        row = data[0]
        assert "chunk_id" in row
        assert row["knowledge_base_id"] == "kb42"
        assert row["source_file"] == "doc.pdf"
        assert row["content_type"] == "text"
        assert row["section_path"] == "Ch1"
        assert row["text"] == "some text"
        assert row["embed_model"] == "text-embedding-3-small"
        assert isinstance(row["vector"], list)
        assert len(row["vector"]) == 1536

    def test_extra_meta_stored_in_json_field(self):
        """不在顶层字段集合内的 metadata 存入 extra_meta JSON。"""
        client = make_mock_client()
        store = _store(client)
        ec = make_embedded(page_number=3, chunk_index=1)
        store.upsert([ec], knowledge_base_id="kb1")
        row = client.upsert.call_args[1]["data"][0]
        assert row["extra_meta"]["page_number"] == 3
        assert row["extra_meta"]["chunk_index"] == 1

    def test_top_level_meta_not_duplicated_in_extra(self):
        """source_file / content_type / section_path 不重复存入 extra_meta。"""
        client = make_mock_client()
        store = _store(client)
        ec = make_embedded()
        store.upsert([ec], knowledge_base_id="kb1")
        row = client.upsert.call_args[1]["data"][0]
        assert "source_file" not in row["extra_meta"]
        assert "content_type" not in row["extra_meta"]
        assert "section_path" not in row["extra_meta"]


# ── error chunk 跳过 ──────────────────────────────────────────────────────────

class TestErrorChunkSkip:

    def test_error_chunk_not_written(self):
        client = make_mock_client()
        store = _store(client)
        ec = make_embedded(content_type="error", embedding=[])
        store.upsert([ec], knowledge_base_id="kb1")
        client.upsert.assert_not_called()

    def test_error_chunk_not_counted(self):
        store = _store()
        ec = make_embedded(content_type="error", embedding=[])
        assert store.upsert([ec], knowledge_base_id="kb1") == 0

    def test_mixed_chunks_error_skipped(self):
        """error chunk 夹在中间，正常 chunk 正常写入。"""
        client = make_mock_client()
        store = _store(client)
        chunks = [
            make_embedded("A"),
            make_embedded("", content_type="error", embedding=[]),
            make_embedded("B"),
        ]
        count = store.upsert(chunks, knowledge_base_id="kb1")
        assert count == 2
        data = client.upsert.call_args[1]["data"]
        assert len(data) == 2


# ── 批处理 ────────────────────────────────────────────────────────────────────

class TestBatching:

    def test_large_input_batched(self):
        """超过 batch_size 时应拆成多批 upsert 调用。"""
        client = make_mock_client()
        store = _store(client, batch_size=3)
        chunks = [make_embedded(f"text {i}") for i in range(10)]
        store.upsert(chunks, knowledge_base_id="kb1")
        assert client.upsert.call_count >= 4

    def test_small_input_single_batch(self):
        client = make_mock_client()
        store = _store(client, batch_size=200)
        chunks = [make_embedded(f"text {i}") for i in range(5)]
        store.upsert(chunks, knowledge_base_id="kb1")
        assert client.upsert.call_count == 1

    def test_total_count_correct_across_batches(self):
        store = _store(batch_size=3)
        chunks = [make_embedded(f"text {i}") for i in range(10)]
        count = store.upsert(chunks, knowledge_base_id="kb1")
        assert count == 10


# ── 幂等性（相同内容产生相同 chunk_id）────────────────────────────────────────

class TestIdempotency:

    def test_same_content_same_chunk_id(self):
        client = make_mock_client()
        store = _store(client)
        ec = make_embedded("same text", source_file="doc.pdf")

        store.upsert([ec], knowledge_base_id="kb1")
        id1 = client.upsert.call_args[1]["data"][0]["chunk_id"]

        store.upsert([ec], knowledge_base_id="kb1")
        id2 = client.upsert.call_args[1]["data"][0]["chunk_id"]

        assert id1 == id2

    def test_different_kb_different_chunk_id(self):
        client = make_mock_client()
        store = _store(client)
        ec = make_embedded("same text")

        store.upsert([ec], knowledge_base_id="kb1")
        id1 = client.upsert.call_args[1]["data"][0]["chunk_id"]

        store.upsert([ec], knowledge_base_id="kb2")
        id2 = client.upsert.call_args[1]["data"][0]["chunk_id"]

        assert id1 != id2


# ── delete_by_source ──────────────────────────────────────────────────────────

class TestDeleteBySource:

    def test_delete_calls_client_delete_when_rows_found(self):
        """query 到行时才调用 delete。"""
        client = make_mock_client()
        # 第一页有数据，第二页为空（终止循环）
        client.query.side_effect = [
            [{"chunk_id": "abc123"}, {"chunk_id": "def456"}],
            [],
        ]
        store = _store(client)
        store.delete_by_source(knowledge_base_id="kb1", source_file="old.pdf")
        assert client.delete.call_count == 1
        assert set(client.delete.call_args[1]["ids"]) == {"abc123", "def456"}

    def test_delete_not_called_when_no_rows(self):
        """query 返回空时不调用 delete。"""
        client = make_mock_client()
        client.query.return_value = []
        store = _store(client)
        store.delete_by_source(knowledge_base_id="kb1", source_file="old.pdf")
        client.delete.assert_not_called()

    def test_query_filter_contains_kb_and_source(self):
        """query 过滤条件包含 kb_id 和 source_file。"""
        client = make_mock_client()
        client.query.return_value = []
        store = _store(client)
        store.delete_by_source(knowledge_base_id="kb42", source_file="report.pdf")
        filter_expr = client.query.call_args[1]["filter"]
        assert "kb42" in filter_expr
        assert "report.pdf" in filter_expr

    def test_paginated_delete_multiple_pages(self):
        """超过 _DELETE_PAGE_SIZE 时分多页删除。"""
        from ingestion.store.milvus_store import _DELETE_PAGE_SIZE
        client = make_mock_client()
        page1 = [{"chunk_id": f"id{i}"} for i in range(_DELETE_PAGE_SIZE)]
        page2 = [{"chunk_id": "last"}]
        page3 = []
        client.query.side_effect = [page1, page2, page3]
        store = _store(client)
        store.delete_by_source(knowledge_base_id="kb1", source_file="big.pdf")
        assert client.delete.call_count == 2


# ── Collection 初始化 ─────────────────────────────────────────────────────────

class TestCollectionInit:

    def test_existing_collection_not_recreated(self):
        """Collection 已存在时不调用 create_collection。"""
        client = make_mock_client()
        client.has_collection.return_value = True
        MilvusStore(config=MilvusStoreConfig(), client=client)
        client.create_collection.assert_not_called()

    def test_missing_collection_is_created(self):
        """Collection 不存在时调用 create_collection。"""
        client = make_mock_client()
        client.has_collection.return_value = False
        # create_schema 需返回可链式调用的对象
        schema_mock = MagicMock()
        client.create_schema.return_value = schema_mock
        index_mock = MagicMock()
        client.prepare_index_params.return_value = index_mock

        MilvusStore(config=MilvusStoreConfig(), client=client)
        client.create_collection.assert_called_once()
