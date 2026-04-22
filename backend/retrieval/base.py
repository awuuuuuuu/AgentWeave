from __future__ import annotations

import asyncio
import functools
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class RetrievedChunk:
    """检索结果，携带多种分数便于调试"""

    chunk_id: str
    text: str
    source_file: str
    section_path: str
    chunk_index_in_doc: int

    vector_score: float = 0.0           # 归一化前的原始余弦相似度
    bm25_score: float = 0.0             # 归一化前的原始 BM25 分数
    fusion_score: float = 0.0           # Weighted Sum 融合后的归一化分数
    rerank_score: float = 0.0           # cross-encoder 精排分数

    retrieval_method: str = "hybrid"    # "vector" | "bm25" | "hybrid"

    extra_meta: dict = field(default_factory=dict)


class BaseRetriever(ABC):
    """检索器抽象基类"""

    @abstractmethod
    def retrieve(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int = 5,
        filter_expr: str | None = None
    ) -> list[RetrievedChunk]: ...

    async def aretrieve(
        self,
        query: str,
        knowledge_base_id: str,
        top_k: int = 5,
        filter_expr: str | None = None
    ) -> list[RetrievedChunk]:
        """
        异步检索
        在系统默认线程池中运行 retrieve()
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self.retrieve, query, knowledge_base_id, top_k, filter_expr)
        )