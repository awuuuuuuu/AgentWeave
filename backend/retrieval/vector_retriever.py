from __future__ import annotations

import logging
from dataclasses import dataclass

from pymilvus import MilvusClient

from ingestion.embedder.base import BaseEmbedder
from ingestion.store.milvus_store import (
    MilvusStoreConfig,
    _F_CHUNK_ID,
    _F_TEXT,
    _F_CONTENT_TYPE,
    _F_SOURCE_FILE,
    _F_SECTION_PATH,
    _F_CHUNK_INDEX,
    _F_EXTRA_META,
    _F_VECTOR
)
from .base import BaseRetriever, RetrievedChunk

logger = logging.getLogger(__name__)

def _kb_filter(knowledge_base_id: str) -> str:
    """构造 knowledge_base_id 过滤表达式，转义值中的双引号防止注入"""
    escaped = knowledge_base_id.replace("\\", "\\\\").replace('"', '\\"')
    return f'knowledge_base_id == "{escaped}"'

_OUTPUT_FIELDS = [
    _F_CHUNK_ID, _F_TEXT, _F_SOURCE_FILE,
    _F_SECTION_PATH, _F_CHUNK_INDEX, _F_CONTENT_TYPE, _F_EXTRA_META
]

@dataclass
class VectorRetrieverConfig:
    store_config: MilvusStoreConfig | None = None
    candidate_multiplier: int = 2

class VectorRetriever(BaseRetriever):
    """Milvus 稠密向量检索器"""

    def __init__(
        self,
        embedder: BaseEmbedder,
        config: VectorRetrieverConfig | None = None,
        client: MilvusClient | None = None
    ) -> None:
        self._embedder = embedder
        self._cfg = config or VectorRetrieverConfig()
        store_cfg = self._cfg.store_config or MilvusStoreConfig()
        self._client = client or MilvusClient(uri=store_cfg.uri)
        self._collection = store_cfg.collection_name
        # metric_type 与建库时保持一致
        self._metric_type = store_cfg.metric_type

    def retrieve(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int = 5,
        filter_expr: str | None = None
    ) -> list[RetrievedChunk]:
        limit = top_k * self._cfg.candidate_multiplier
        query_vec = self._embedder.embed_query(query)

        kb_expr = _kb_filter(knowledge_base_id)
        expr = f"({kb_expr}) and ({filter_expr})" if filter_expr else kb_expr

        ef = max(64, limit)
        results = self._client.search(
            collection_name=self._collection,
            data=[query_vec],
            anns_field=_F_VECTOR,
            limit=limit,
            filter=expr,
            search_params={
                "metric_type": self._metric_type,
                "params":{
                    "ef": ef
                }
            },
            output_fields=_OUTPUT_FIELDS
        )

        hits = results[0] if results else []
        chunks = []
        for hit in hits:
            e = hit["entity"]
            chunks.append(RetrievedChunk(
                chunk_id=e[_F_CHUNK_ID],
                text=e[_F_TEXT],
                source_file=e[_F_SOURCE_FILE],
                section_path=e[_F_SECTION_PATH],
                chunk_index_in_doc=int(e.get(_F_CHUNK_INDEX, 0)),
                vector_score=float(hit["distance"]),
                retrieval_method="vector",
                extra_meta=e.get(_F_EXTRA_META) or {},
            ))
            
        logger.debug("VectorRetriever: query=%r kb=%s 返回 %d 条", query[:50], knowledge_base_id, len(chunks))
        return chunks