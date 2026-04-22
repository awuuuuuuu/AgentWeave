from __future__ import annotations

import logging
from dataclasses import dataclass

from pymilvus import MilvusClient

from ingestion.store.milvus_store import (
    MilvusStoreConfig,
    _F_CHUNK_ID,
    _F_TEXT,
    _F_SOURCE_FILE,
    _F_SECTION_PATH,
    _F_CHUNK_INDEX,
    _F_EXTRA_META,
    _F_SPARSE_VECTOR,
)

from .base import BaseRetriever, RetrievedChunk

logger = logging.getLogger(__name__)

_OUTPUT_FIELDS = [
    _F_CHUNK_ID, _F_TEXT, _F_SOURCE_FILE,
    _F_SECTION_PATH, _F_CHUNK_INDEX, _F_EXTRA_META
]

@dataclass
class BM25RetrieverConfig:
    store_config: MilvusStoreConfig | None = None
    candidate_multiplier: int = 2           # 候选者倍增器
    drop_ratio_search: float = 0.2          # 动态停用词剔除, 用于降噪


class BM25Retriever(BaseRetriever):
    """
    使用 Milvus 内置的 BM25 稀疏向量检索器进行检索

    稀疏向量由 Milvus BM25 Function 在 insert 时自动从 text 生成
    查询时同样由 Milvus 处理文本→稀疏向量转换, Python 侧只需传入原始 query 字符串
    """

    def __init__(
        self,
        config: BM25RetrieverConfig | None = None,
        client: MilvusClient | None = None
    ) -> None:
        self._cfg = config or BM25RetrieverConfig()
        store_cfg = self._cfg.store_config or MilvusStoreConfig()
        if not store_cfg.enable_bm25:
            raise ValueError(
                "BM25Retriever 要求 MilvusStoreConfig.enable_bm25=True，"
                "当前 collection 未启用 BM25 Function，无法搜索 sparse_vector。"
            )
        self._client = client or MilvusClient(uri=store_cfg.uri)
        self._collection = store_cfg.collection_name

    def retrieve(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int = 5,
        filter_expr: str | None = None
    ) -> list[RetrievedChunk]:
        limit = top_k * self._cfg.candidate_multiplier

        expr = f"(knowledge_base_id == {knowledge_base_id}) and ({filter_expr})" if filter_expr else f"knowledge_base_id == {knowledge_base_id}"

        results = self._client.search(
            collection_name=self._collection,
            data=[query],
            anns_field=_F_SPARSE_VECTOR,
            limit=limit,
            filter=expr,
            search_params={
                "metric_type": "BM25",
                "params": {"drop_ratio_search": self._cfg.drop_ratio_search}
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
                bm25_score=float(hit["distance"]),
                retrieval_method="bm25",
                extra_meta=e.get(_F_EXTRA_META) or {}
            ))

        logger.debug("BM25Retriever: query=%r kb=%s 返回 %d 条", query[:80], knowledge_base_id, len(chunks))
        return chunks
