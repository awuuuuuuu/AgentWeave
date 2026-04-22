from __future__ import annotations

import logging
from dataclasses import dataclass

import torch

from .base import RetrievedChunk

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_RERANK_LIMIT = 20          # 送入 cross-encoder 的最大候选数

@dataclass
class RerankerConfig:
    model_name: str = _DEFAULT_MODEL
    rerank_limit: int = _RERANK_LIMIT
    use_gpu: bool = False

class Reranker:
    """Cross-encoder 精排。

    将 HybridRetriever 的候选集送入 cross-encoder，得到精排分后重新排序
    """

    def __init__(
        self,
        config: RerankerConfig | None = None
    ) -> None:
        self._cfg = config or RerankerConfig()
        self._model = None
    
    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None
    ) -> list[RetrievedChunk]:
        """对候选集进行 cross-encoder 精排，返回按 rerank_score 降序排列的结果"""
        if not chunks:
            return []
        
        self._ensure_model()

        candidates = chunks[: self._cfg.rerank_limit]
        pairs = [[query, c.text] for c in candidates]

        raw_results = self._model.predict(pairs)
        scores: list[float] = raw_results.tolist() if hasattr(raw_results, "tolist") else list(raw_results)

        if len(scores) != len(candidates):
            raise RuntimeError(
                f"Reranker predict() 返回 {len(scores)} 个分数，"
                f"但候选数为 {len(candidates)}，模型输出异常。"
            )
        
        for chunk, score in zip(candidates, scores):
            chunk.rerank_score = float(score)
        
        candidates.sort(key=lambda c: c.rerank_score, reverse=True)
        logger.debug("Reranker: query=%r 精排 %d 条", query[:50], len(candidates))

        return candidates[:top_k] if top_k is not None else candidates

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as e:
            raise ImportError(
                "sentence-transformers 未安装，请执行: uv add sentence-transformers"
            ) from e
        
        device = "cpu"
        if self._cfg.use_gpu and torch.cuda.is_available():
            device = "cuda"
            
        self._model = CrossEncoder(self._cfg.model_name, device=device)
        logger.info("Reranker 模型已加载：%s（device=%s）", self._cfg.model_name, device)

        
