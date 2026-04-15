"""
MilvusStore 集成测试（需要真实 Milvus 实例）

前置条件：
  docker run -d --name milvus-standalone -p 19530:19530 -p 9091:9091 \\
    milvusdb/milvus:v2.5.0 standalone

运行：
  uv run pytest backend/tests/store/test_milvus_store_integration.py -v -s

CI 中跳过：pytest -m "not integration"
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from pymilvus import MilvusClient

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from ingestion.store import MilvusStore, MilvusStoreConfig

from .conftest import make_embedded

pytestmark = pytest.mark.integration

_MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
_TEST_COLLECTION = f"test_ragent_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def store() -> MilvusStore:
    try:
        client = MilvusClient(uri=_MILVUS_URI)
        client.list_collections()  # 连通性检查
    except Exception:
        pytest.skip(f"无法连接 Milvus（{_MILVUS_URI}），跳过集成测试")

    cfg = MilvusStoreConfig(
        uri=_MILVUS_URI,
        collection_name=_TEST_COLLECTION,
        vector_dim=3,  # 集成测试用小维度，节省内存
    )
    s = MilvusStore(config=cfg)
    yield s

    # 清理测试 Collection
    try:
        s._client.drop_collection(_TEST_COLLECTION)
    except Exception:
        pass


class TestRealMilvus:

    def test_upsert_single_chunk(self, store):
        ec = make_embedded("测试文本", embedding=[0.1, 0.2, 0.3])
        count = store.upsert([ec], knowledge_base_id="kb_test")
        assert count == 1

    def test_upsert_multiple_chunks(self, store):
        chunks = [
            make_embedded(f"文本 {i}", embedding=[float(i), 0.0, 0.0])
            for i in range(5)
        ]
        count = store.upsert(chunks, knowledge_base_id="kb_test")
        assert count == 5

    def test_upsert_idempotent(self, store):
        """相同内容写入两次，row 数不增加（upsert 覆盖）。"""
        ec = make_embedded("幂等测试", embedding=[0.5, 0.5, 0.0], source_file="idempotent.pdf")
        store.upsert([ec], knowledge_base_id="kb_idem")
        store.upsert([ec], knowledge_base_id="kb_idem")

        results = store._client.query(
            collection_name=_TEST_COLLECTION,
            filter='source_file == "idempotent.pdf"',
            output_fields=["chunk_id"],
            consistency_level="Strong",
        )
        assert len(results) == 1

    def test_error_chunk_not_written(self, store):
        ec = make_embedded("", content_type="error", embedding=[])
        count = store.upsert([ec], knowledge_base_id="kb_test")
        assert count == 0

    def test_delete_by_source(self, store):
        source = f"delete_test_{uuid.uuid4().hex[:6]}.pdf"
        chunks = [
            make_embedded(f"内容 {i}", embedding=[0.1, 0.2, 0.3], source_file=source)
            for i in range(3)
        ]
        store.upsert(chunks, knowledge_base_id="kb_del")

        store.delete_by_source(knowledge_base_id="kb_del", source_file=source)

        results = store._client.query(
            collection_name=_TEST_COLLECTION,
            filter=f'source_file == "{source}"',
            output_fields=["chunk_id"],
            consistency_level="Strong",
        )
        assert len(results) == 0

    def test_extra_meta_persisted(self, store):
        source = f"extra_{uuid.uuid4().hex[:6]}.pdf"
        ec = make_embedded(
            "有额外 metadata 的 chunk",
            embedding=[0.3, 0.3, 0.4],
            source_file=source,
            page_number=7,
        )
        store.upsert([ec], knowledge_base_id="kb_meta")

        results = store._client.query(
            collection_name=_TEST_COLLECTION,
            filter=f'source_file == "{source}"',
            output_fields=["extra_meta"],
            consistency_level="Strong",
        )
        assert len(results) == 1
        assert results[0]["extra_meta"]["page_number"] == 7
