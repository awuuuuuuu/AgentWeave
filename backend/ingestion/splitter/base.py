from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import tiktoken

from ..parsers.base import ParsedChunk

TABLE_HARD_LIMIT = 2048  # token 数超过此值的 table chunk 强制切分


class BaseSplitter(ABC):
    """所有切分策略的抽象基类。

    约定：
    - title：恒定透传，不切分
    - table：token 数 ≤ TABLE_HARD_LIMIT 时透传；超限则交给 _split_chunk 强制切分
    - sub-chunk 继承父 chunk 所有 metadata，追加 chunk_index / chunk_total
    """

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self._enc = tiktoken.get_encoding(encoding_name)

    def split(self, chunks: Sequence[ParsedChunk]) -> list[ParsedChunk]:
        result: list[ParsedChunk] = []
        for chunk in chunks:
            ct = chunk.metadata.get("content_type")
            if ct == "title":
                result.append(chunk)
            elif ct == "table" and self.count_tokens(chunk.text) <= TABLE_HARD_LIMIT:
                result.append(chunk)
            else:
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