from __future__ import annotations

from dataclasses import dataclass, field

from ..parsers.base import ParsedChunk
from .base import BaseSplitter

_SEPARATORS = ["\n\n", "\n", "。", ".", "；", ";", " ", ""]

@dataclass
class RecursiveConfig:
    chunk_size: int = 1024
    chunk_overlap: int = 128
    separators: list[str] = field(default_factory=lambda: list(_SEPARATORS))
    encoding_name: str = "cl100k_base"

class RecursiveSplitter(BaseSplitter):
    """
    递归字符切分（默认切分方式）

    1. 从颗粒度高到低尝试分隔符列表
    2. 切分后 merge 短chunk到接近chunk_size
    3. 相邻的块间保留chunk_overlap 的重叠
    """

    def __init__(self, config: RecursiveConfig | None = None) -> None:
        cfg = config or RecursiveConfig()
        super().__init__(cfg.encoding_name)
        self.config = cfg

    def _split_chunk(self, chunk: ParsedChunk) -> list[ParsedChunk]:
        if self.count_tokens(chunk.text) <= self.config.chunk_size:
            return [chunk]
        
        texts = self._recursive_split(chunk.text, self.config.separators)
        merged = self._merge(texts)

        return self._make_sub_chunks(merged, chunk) if merged else [chunk]
    
    def _recursive_split(self, text: str, separators: list[str]) -> list[str]:
        if not separators or separators[0] == "":
            return self._split_by_tokens(text)
        
        sep, remaining = separators[0], separators[1:]
        parts = text.split(sep)
        parts_with_sep = [p + sep for p in parts[:-1]] + [parts[-1]]


        results: list[str] = []
        for part in parts_with_sep:
            if not part.strip():
                continue
            if self.count_tokens(part) <= self.config.chunk_size:
                results.append(part)
            else:
                results.extend(self._recursive_split(part, remaining))
        return results      


    def _split_by_tokens(self, text: str) -> list[str]:
        tokens = self.encode(text)
        return [
            self.decode(tokens[i: i + self.config.chunk_size])
            for i in range(0, len(tokens), self.config.chunk_size)
        ]
    
    def _merge(self, texts: list[str]) -> list[str]:
        merged: list[str] = []
        current: list[int] = []

        for text in texts:
            new = self.encode(text)
            if len(current) + len(new) <= self.config.chunk_size:
                current.extend(new)
            else:
                if current:
                    merged.append(self.decode(current))

                    overlap_size = min(
                        self.config.chunk_overlap,
                        max(0, self.config.chunk_size - len(new))
                    )

                    current = current[-overlap_size:] if overlap_size > 0 else []
                current.extend(new)

        if current:
            merged.append(self.decode(current))
        return merged