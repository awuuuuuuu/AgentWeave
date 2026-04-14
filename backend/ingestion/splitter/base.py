from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import tiktoken

from ..parsers.base import ParsedChunk

# 不切分的类型
NO_SPLIT_TYPES = {"title", "table"}

class BaseSplitter(ABC):
    """
    所有切分策略的抽象基类。

    约定：
    - table / title 类型直接透传，不切分
    - sub-chunk 继承父 chunk 所有 metadata，追加 chunk_index / chunk_total
    """

    def __init__(self, encoing_name: str = "cl100k_base") -> None:
        self._enc = tiktoken.get_encoding(encoing_name)
    
    def split(self, chunks: Sequence[ParsedChunk]) -> list[ParsedChunk]:
        result: list[ParsedChunk] = []
        for chunk in chunks:
            result.extend(self._split_chunk(chunk))
        return result
    
    @abstractmethod
    def _split_chunk(self, chunk: ParsedChunk) -> list[ParsedChunk]:
        """子类实现具体切分逻辑"""



    def count_tokens(self, text: str) -> int:
        return len(self._enc.encode(text))
    
    def encode(self, text: str) -> list[int]:
        return self._enc.encode(text)
    
    def decode(self, tokens: list[int]) -> str:
        return self._enc.decode(tokens)
    
    def _make_sub_chunks(
        self, texts: list[str], parent: ParsedChunk
    ) -> list[ParsedChunk]:
        """将文本列表包装为 ParsedChunk，继承父 chunk 的所有 metadata"""
        total = len(texts)
        return [
            ParsedChunk(
                text=text,
                metadata={**parent.metadata, "chunk_index": i, "chunk_total": total}
            )
            for i, text in enumerate(texts)
        ]