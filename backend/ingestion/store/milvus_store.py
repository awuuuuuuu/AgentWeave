from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from pymilvus import DataType, MilvusClient

from ..embedder.base import EmbeddedChunk

logger = logging.getLogger(__name__)


# Milvus 数据库名称
_DEFAULT_COLLECTION = "ragent_chunks"

# Milvus 数据库的 Schema
_F_CHUNK_ID       = "chunk_id"           # VARCHAR 主键，确定性 hash
_F_KB_ID          = "knowledge_base_id"  # VARCHAR，Partition Key（分区键）
_F_SOURCE_FILE    = "source_file"        # VARCHAR，scalar index（标量索引）
_F_CONTENT_TYPE   = "content_type"       # VARCHAR，scalar index（标量索引）
_F_SECTION_PATH   = "section_path"       # VARCHAR
_F_EMBED_MODEL    = "embed_model"        # VARCHAR
_F_TEXT           = "text"               # VARCHAR，原始 chunk 文本
_F_EXTRA_META     = "extra_meta"         # JSON，其余非结构化 metadata
_F_VECTOR         = "vector"             # FLOAT_VECTOR

# 已提升为顶层字段的 metadata key，不再重复存入 extra_meta
_TOP_LEVEL_META_KEYS = {"source_file", "content_type", "section_path"}

# VARCHAR 字节上限
_TEXT_MAX_BYTES    = 65_535
_VARCHAR_MAX_BYTES = 65_535
_SECTION_MAX_BYTES = 2_048
_KB_ID_MAX_BYTES   = 512

# delete_by_source 分页大小
_DELETE_PAGE_SIZE = 1_000

@dataclass
class MilvusStoreConfig:
    uri: str = "http://localhost:19530"
    collection_name: str = _DEFAULT_COLLECTION
    vector_dim: int = 1536
    index_type: str = "HNSW"
    metric_type: str = "COSINE"
    hnsw_m: int = 16
    hnsw_ef_construction: int = 128
    batch_size: int = 200


class MilvusStore:
    """
    Milvus 向量数据库引擎

    特点：
    1. chunk_id：SHA256(kb_id + source_file + text) 前 32 位
    2. Partition Key（分区键）按 knowledge_base_id 分区，隔离多租户数据
    3. metadata 的常用字段上升为顶层建立 INVERTED 索引
    """

    def __init__(
        self,
        config: MilvusStoreConfig | None = None,
        client: MilvusClient | None = None
    ) -> None:
        self._cfg = config or MilvusStoreConfig()
        self._client = client or MilvusClient(uri=self._cfg.uri)
        self._ensure_collection()


    def upsert(
        self,
        embedded_chunks: list[EmbeddedChunk],
        knowledge_base_id: str
    ) -> int:
        """
        将 EmbeddedChunks 添加到 Milvus
        """
        rows = [
            self._to_row(ec, knowledge_base_id)
            for ec in embedded_chunks
            if not ec.skipped
        ]
        if not rows:
            return 0
        
        total = 0
        for start in range(0, len(rows), self._cfg.batch_size):
            batch = rows[start : start + self._cfg.batch_size]
            self._client.upsert(
                collection_name=self._cfg.collection_name,
                data=batch
            )
            total += len(batch)
            logger.debug("已写入 %d / %d 条", total, len(rows))

        return total
    
    def delete_by_source(
        self,
        knowledge_base_id: str,
        source_file: str
    ) -> None:
        """
        删除指定知识库的某个源文件的所有chunk
        """
        expr = (
            f'{_F_KB_ID} == "{knowledge_base_id}" '
            f'and {_F_SOURCE_FILE} == "{source_file}"'
        )
        while True:
            rows = self._client.query(
                collection_name=self._cfg.collection_name,
                filter=expr,
                output_fields=[_F_CHUNK_ID],
                limit=_DELETE_PAGE_SIZE,
                consistency_level="Strong"
            )
            ids = [r[_F_CHUNK_ID] for r in rows]
            if not ids:
                break
            self._client.delete(
                collection_name=self._cfg.collection_name,
                ids=ids
            )
            if len(rows) < _DELETE_PAGE_SIZE:
                break
    
    def _ensure_collection(self) -> None:
        """若 Collection 不存在则创建，已存在则跳过"""
        if self._client.has_collection(self._cfg.collection_name):
            return
        
        schema = self._client.create_schema(
            auto_id=False,
            enable_dynamic_field=False
        )
        schema.add_field(_F_CHUNK_ID,     DataType.VARCHAR, max_length=64,                  is_primary=True)
        schema.add_field(_F_KB_ID,        DataType.VARCHAR, max_length=_KB_ID_MAX_BYTES,    partition_key=True)
        schema.add_field(_F_SOURCE_FILE,  DataType.VARCHAR, max_length=_VARCHAR_MAX_BYTES)
        schema.add_field(_F_CONTENT_TYPE, DataType.VARCHAR, max_length=128)
        schema.add_field(_F_SECTION_PATH, DataType.VARCHAR, max_length=_SECTION_MAX_BYTES)
        schema.add_field(_F_EMBED_MODEL,  DataType.VARCHAR, max_length=128)
        schema.add_field(_F_TEXT,         DataType.VARCHAR, max_length=_TEXT_MAX_BYTES)
        schema.add_field(_F_EXTRA_META,   DataType.JSON)
        schema.add_field(_F_VECTOR,       DataType.FLOAT_VECTOR, dim=self._cfg.vector_dim)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name=_F_VECTOR,
            index_type=self._cfg.index_type,
            metric_type=self._cfg.metric_type,
            params={"M": self._cfg.hnsw_m, "efConstruction": self._cfg.hnsw_ef_construction}
        )

        for fname in (_F_SOURCE_FILE, _F_CONTENT_TYPE):
            index_params.add_index(field_name=fname, index_type="INVERTED")

        self._client.create_collection(
            collection_name=self._cfg.collection_name,
            schema=schema,
            index_params=index_params
        )
        logger.info("Milvus Collection '%s' 创建完成", self._cfg.collection_name)

        
    def _to_row(self, ec: EmbeddedChunk, knowledge_base_id: str) -> dict:
        """把 EmbeddedChunk 转成 Milvus 一行数据。"""
        metadata = ec.chunk.metadata
        source_file  = metadata.get("source_file", "")
        content_type = metadata.get("content_type", "")
        section_path = metadata.get("section_path", "")

        extra_raw = {k : v for k, v in metadata.items() if k not in _TOP_LEVEL_META_KEYS}
        extra_json = json.loads(json.dumps(extra_raw, default=str))

        return {
            _F_CHUNK_ID:      _make_chunk_id(knowledge_base_id, source_file, ec.chunk.text),
            _F_KB_ID:         _truncate_bytes(knowledge_base_id, _KB_ID_MAX_BYTES),
            _F_SOURCE_FILE:   _truncate_bytes(source_file, _VARCHAR_MAX_BYTES),
            _F_CONTENT_TYPE:  _truncate_bytes(content_type, 128),
            _F_SECTION_PATH:  _truncate_bytes(section_path, _SECTION_MAX_BYTES),
            _F_EMBED_MODEL:   _truncate_bytes(ec.embed_model, 128),
            _F_TEXT:          _truncate_bytes(ec.chunk.text, _TEXT_MAX_BYTES),
            _F_EXTRA_META:    extra_json,
            _F_VECTOR:        ec.embedding,
        }

def _make_chunk_id(knowledge_base_id: str, source_file: str, text: str) -> str:
    """
    chunk_id：SHA256(kb_id + source_file + text) 前 32 个十六进制字符。
    """
    payload = f"{knowledge_base_id}\x00{source_file}\x00{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

def _truncate_bytes(text: str, max_bytes: int) -> str:
    """
    防止字符串字符过长超过 Milvus VARCHAR 限制
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")