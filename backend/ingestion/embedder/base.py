from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import cast

from ..parsers.base import ParsedChunk

@dataclass
class EmbeddedChunk:
    """Embedder

    chunk       : 原始 ParsedChunk
    embedding   : 向量
    embed_model : 实际调用的模型名
    """

    chunk: ParsedChunk
    embedding: list[float] = field(default_factory=list)
    embed_model: str = ""

    @property
    def skipped(self) -> bool:
        """是否跳过了embedding"""
        return len(self.embedding) == 0
    

class BaseEmbedder(ABC):
    """
    Embedder 的抽象基类

    基类实现分批循环，子类接收到的一定 ≤ batch_size 条文本
    """

    def __init__(self, batch_size: int = 512) -> None:
        self._batch_size = batch_size
    
    def embed(self, chunks: list[ParsedChunk]) -> list[EmbeddedChunk]:
        """按 batch_size 分批 embed"""
        if not chunks:
            return []
        
        results: list[EmbeddedChunk | None] = [None] * len(chunks)
        embeddable: list[tuple[int, ParsedChunk]] = []

        for i, chunk in enumerate(chunks):
            if chunk.metadata.get("content_type") == "error":
                results[i] = EmbeddedChunk(chunk=chunk)
            else:
                embeddable.append((i, chunk))
        
        if embeddable:
            indices, to_embed = zip(*embeddable)
            texts = [c.text for c in to_embed]
            
            all_vectors: list[list[float]] = []
            for start in range(0, len(texts), self._batch_size):
                batch = texts[start: start + self._batch_size]
                all_vectors.extend(self._embed_texts(batch))
            
            for idx, chunk, vec in zip(indices, to_embed, all_vectors):
                results[idx] = EmbeddedChunk(
                    chunk=chunk,
                    embedding=vec,
                    embed_model=self.model_name
                )

        return cast(list[EmbeddedChunk], results)
    
    def embed_query(self, query: str) -> list[float]:
        """将单条查询文本向量化，供检索层使用。"""
        result = self._embed_texts([query])
        if not result or not result[0]:
            raise ValueError(f"embed_query 返回空向量，query={query!r:.50}")
        return result[0]

    @abstractmethod
    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """子类实现：接收 ≤ batch_size 条纯文本，返回等长向量列表"""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """使用的 embedding 模型名称"""