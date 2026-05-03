from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ingestion.embedder.base import BaseEmbedder
from ingestion.store.milvus_store import MilvusStoreConfig
from .base import BaseRetriever, RetrievedChunk
from .bm25_retriever import BM25Retriever, BM25RetrieverConfig
from .vector_retriever import VectorRetriever, VectorRetrieverConfig

# 双路并发专用线程池：固定 2 个 worker，一个跑 vector，一个跑 BM25
_FUSE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hybrid_fuse")

logger = logging.getLogger(__name__)

@dataclass
class HybridRetrieverConfig:
    store_config: MilvusStoreConfig | None = None
    alpha: float = 0.7                      # Weighted Sum 权重
    candidate_multiplier: int = 2           # 每路 Retriever 的候选倍数
    fallback_multiplier: int = 4            # 零结果时放宽阈值后的候选倍数

class HybridRetriever(BaseRetriever):
    """
    混合检索器：稠密向量 + BM25 稀疏向量, Query-level 归一后 Weighted Sum 融合

    检索策略：
        1. fusion_score = alpha × norm(vector_score) + (1-alpha) × norm(bm25_score)
        2. 若融合后候选为空, 自动扩大候选数重试一次（fallback_multiplier）
    """

    def __init__(
        self,
        embedder: BaseEmbedder,
        config: HybridRetrieverConfig | None = None
    ) -> None:
        self._cfg = config or HybridRetrieverConfig()
        store_cfg = self._cfg.store_config or MilvusStoreConfig()

        # candidate_multiplier 统一由 HybridRetriever 控制，
        # 子 retriever 固定为 1，避免双重放大（top_k * multiplier * multiplier）
        vec_cfg = VectorRetrieverConfig(
            store_config=store_cfg,
            candidate_multiplier=1
        )
        bm25_cfg = BM25RetrieverConfig(
            store_config=store_cfg,
            candidate_multiplier=1
        )
        self._vector = VectorRetriever(embedder=embedder, config=vec_cfg)
        self._bm25 = BM25Retriever(config=bm25_cfg)

    def retrieve(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int = 5,
        filter_expr: str | None = None
    ) -> list[RetrievedChunk]:
        results = self._fuse(query, knowledge_base_id, top_k,
                             self._cfg.candidate_multiplier, filter_expr)
        if not results and self._cfg.fallback_multiplier > self._cfg.candidate_multiplier:
            logger.warning("HybridRetriever: 零结果，扩大候选数重试（multiplier=%d）",
                           self._cfg.fallback_multiplier)
            results = self._fuse(query, knowledge_base_id, top_k,
                                 self._cfg.fallback_multiplier, filter_expr)

        return results[:top_k]

    async def aretrieve(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int = 5,
        filter_expr: str | None = None,
        alpha: float | None = None,
    ) -> list[RetrievedChunk]:
        """异步检索：双路 aretrieve 并发执行，总耗时取决于较慢的一路。
        alpha: 语义权重覆盖，None 则使用 config 默认值。
        """
        results = await self._afuse(query, knowledge_base_id, top_k,
                                    self._cfg.candidate_multiplier, filter_expr, alpha)
        if not results and self._cfg.fallback_multiplier > self._cfg.candidate_multiplier:
            logger.warning("HybridRetriever: 零结果，扩大候选数重试（multiplier=%d）",
                           self._cfg.fallback_multiplier)
            results = await self._afuse(query, knowledge_base_id, top_k,
                                        self._cfg.fallback_multiplier, filter_expr, alpha)
        return results[:top_k]
    

    def _fuse(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int,
        multiplier: int,
        filter_expr: str | None = None
    ) -> list[RetrievedChunk]:
        """
        同步双路并发融合，使用线程池同时跑 vector + BM25
        """
        fetch_k = top_k * multiplier

        fut_vec = _FUSE_EXECUTOR.submit(self._vector.retrieve, query, knowledge_base_id, fetch_k, filter_expr)
        fut_bm25 = _FUSE_EXECUTOR.submit(self._bm25.retrieve, query, knowledge_base_id, fetch_k, filter_expr)
        vec_results = fut_vec.result()
        bm25_results = fut_bm25.result()
        
        return self._merge_and_score(vec_results, bm25_results)
    
    async def _afuse(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int,
        multiplier: int,
        filter_expr: str | None = None,
        alpha: float | None = None,
    ) -> list[RetrievedChunk]:
        """异步双路并发融合，用 asyncio.gather 同时跑 vector + BM25"""
        fetch_k = top_k * multiplier
        vec_results, bm25_results = await asyncio.gather(
            self._vector.aretrieve(query, knowledge_base_id, fetch_k, filter_expr),
            self._bm25.aretrieve(query, knowledge_base_id, fetch_k, filter_expr)
        )
        return self._merge_and_score(vec_results, bm25_results, alpha=alpha)

    def _merge_and_score(
        self,
        vec_results: list[RetrievedChunk],
        bm25_results: list[RetrievedChunk],
        alpha: float | None = None,
    ) -> list[RetrievedChunk]:
        """合并双路结果，Query-level 归一后进行 Weighted Sum 融合"""
        merged: dict[str, RetrievedChunk] = {}
        for chunk in vec_results:
            merged[chunk.chunk_id] = chunk
        for chunk in bm25_results:
            if chunk.chunk_id in merged:
                merged[chunk.chunk_id].bm25_score = chunk.bm25_score
            else:
                merged[chunk.chunk_id] = chunk

        if not merged:
            return []
        
        chunks = list(merged.values())

        # Query-level 归一化
        vec_norm = _normalize([c.vector_score for c in chunks])
        bm25_norm = _normalize([c.bm25_score for c in chunks])

        alpha = alpha if alpha is not None else self._cfg.alpha
        for chunk, vec_score, bm25_score in zip(chunks, vec_norm, bm25_norm):
            chunk.fusion_score = alpha * vec_score + (1 - alpha) * bm25_score
            chunk.retrieval_method = "hybrid"

        chunks.sort(key=lambda c: c.fusion_score, reverse=True)
        logger.debug("HybridRetriever: 融合后 %d 条", len(chunks))
        return chunks
    
def _normalize(scores: list[float]) -> list[float]:
    """Query-level min-max 归一化到 [0, 1]"""
    if not scores:
        return []
    min_s, max_s = min(scores), max(scores)
    if min_s == max_s:
        return [1.0] * len(scores)
    span = max_s - min_s
    return [(s - min_s) / span for s in scores]