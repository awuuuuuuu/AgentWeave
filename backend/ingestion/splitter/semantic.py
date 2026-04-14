from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable

from ..parsers.base import ParsedChunk
from .base import NO_SPLIT_TYPES, BaseSplitter

logger = logging.getLogger(__name__)

EmbedFn = Callable[[list[str]], list[list[float]]]


@dataclass
class SemanticConfig:
    chunk_size: int = 512
    breakpoint_threshold: float = 0.7
    encoding_name: str = "cl100k_base"

class SemanticSplitter(BaseSplitter):
    """
    语义切分：在话题转换处切分

    流程：
    1. 按句子边界（。/ . / \\n）分割文本
    2. 调用 embed_fn 对每句话向量化
    3. 相邻句子余弦相似度 < breakpoint_threshold → 切分点
    """

    def __init__(self, embed_fn: EmbedFn, config: SemanticConfig | None = None) -> None:
        cfg = config or SemanticConfig()
        super().__init__(cfg.encoding_name)
        self.embed_fn = embed_fn
        self.config = cfg
    
    def _split_chunk(self, chunk: ParsedChunk) -> list[ParsedChunk]:
        if chunk.metadata.get("content_type") in NO_SPLIT_TYPES:
            return [chunk]
        
        if self.count_tokens(chunk.text) <= self.config.chunk_size:
            return [chunk]
        
        sentences = _split_sentences(chunk.text)
        if len(sentences) <= 1:
            return [chunk]
        
        try:
            embeddings = self.embed_fn(sentences)
        except Exception as exc:
            logger.warning("SemanticSplitter embed_fn 失败，降级返回原 chunk: %s", exc)
            return [chunk]
        
        groups: list[list[str]] = [[sentences[0]]]
        for i in range(1, len(sentences)):
            sim = _cosine(embeddings[i - 1], embeddings[i])
            if sim < self.config.breakpoint_threshold:
                groups.append([])
            groups[-1].append(sentences[i])
        
        texts = ["".join(g) for g in groups if g]
        return self._make_sub_chunks(texts, chunk) if len(texts) > 1 else [chunk]
    
def _split_sentences(text: str) -> list[str]:
    """按照句子边界进行切分"""
    import re
    parts = re.split(r"(?<=[。.！？!?\n])", text)
    return [p.strip() for p in parts if p.strip()]
    
def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)