from __future__ import annotations

import logging
from dataclasses import dataclass

from .base import RetrievedChunk

logger = logging.getLogger(__name__)

@dataclass
class RerankerConfig:
    reranker_type: str = "dashscope"          # "dashscope" | "local"
    model_name: str = "qwen3-rerank"
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com"
    rerank_limit: int = 20
    # local cross-encoder only
    use_gpu: bool = False


class Reranker:
    """精排器，支持 DashScope 官方 API（qwen3-rerank 等）和本地 cross-encoder 两种模式"""

    def __init__(self, config: RerankerConfig | None = None) -> None:
        self._cfg = config or RerankerConfig()
        self._local_model = None  # lazy-load，仅 local 模式使用

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []

        candidates = chunks[: self._cfg.rerank_limit]

        if self._cfg.reranker_type == "dashscope":
            scores = self._rerank_dashscope(query, [c.text for c in candidates])
        else:
            scores = self._rerank_local(query, [c.text for c in candidates])

        for chunk, score in zip(candidates, scores):
            chunk.rerank_score = float(score)
            chunk.fusion_score = float(score)   # 覆盖展示分，使 UI 显示精排分

        candidates.sort(key=lambda c: c.rerank_score, reverse=True)
        logger.debug("Reranker(%s): 精排 %d 条", self._cfg.reranker_type, len(candidates))
        return candidates[:top_k] if top_k is not None else candidates

    # ── DashScope ────────────────────────────────────────────────────────────

    def _rerank_dashscope(self, query: str, documents: list[str]) -> list[float]:
        """调用 DashScope 的兼容 API (Compatible API)，专为 qwen3-rerank 等新模型设计"""
        if not self._cfg.api_key:
            raise ValueError(
                "DashScope API key 未配置，请在 .env 中设置 DASHSCOPE_API_KEY"
            )

        import httpx

        # 强制使用阿里云最新的 OpenAI 兼容端点
        base = self._cfg.base_url.rstrip("/")
        url = f"{base}/compatible-api/v1/reranks"

        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self._cfg.model_name,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }

        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=payload, headers=headers)
            if not resp.is_success:
                raise RuntimeError(f"DashScope Reranker API {resp.status_code}: {resp.text}")
            
            data = resp.json()

        raw_results = data.get("results", [])
        
        scores = [0.0] * len(documents)
        for item in raw_results:
            scores[item["index"]] = float(item["relevance_score"])
            
        return scores

    # ── Local cross-encoder ───────────────────────────────────────────────────

    def _rerank_local(self, query: str, documents: list[str]) -> list[float]:
        self._ensure_local_model()
        pairs = [[query, doc] for doc in documents]
        raw = self._local_model.predict(pairs)
        return raw.tolist() if hasattr(raw, "tolist") else list(raw)

    def _ensure_local_model(self) -> None:
        if self._local_model is not None:
            return
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as e:
            raise ImportError(
                "sentence-transformers 未安装，请执行: uv add sentence-transformers"
            ) from e
        
        import torch
        device = "cuda" if self._cfg.use_gpu and torch.cuda.is_available() else "cpu"
        self._local_model = CrossEncoder(self._cfg.model_name, device=device)
        logger.info("本地 Reranker 已加载：%s（device=%s）", self._cfg.model_name, device)