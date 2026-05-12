from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import tiktoken

from .base import BaseEmbedder

logger = logging.getLogger(__name__)

_MODEL_MAX_TOKENS: dict[str, int] = {
    "text-embedding-3-small": 8191,
    "text-embedding-3-large": 8191,
    "text-embedding-ada-002": 8191,
}
_DEFAULT_MAX_TOKENS = 8191

# OpenAI 单次请求的 token 总量上限
_MAX_TOKENS_PER_REQUEST = 300_000


@dataclass
class OpenAIEmbedderConfig:
    model: str = "text-embedding-3-small"
    batch_size: int = 512
    max_retries: int = 3
    retry_base_delay: float = 1.0
    encoding_name: str = "cl100k_base"

class OpenAIEmbedder(BaseEmbedder):
    """
    OpenAI Embedding

    特性:
    - 单条或者单批次超过模型上限会被截断
    - 指数退避重试：419 / 5xx 自动重试

    TODO:
    等待 step 3 引入Redis后，在外部使用 CachedEmbedder 包装此类
    """

    def __init__(
        self,
        config: OpenAIEmbedderConfig | None = None,
        client = None
    ) -> None:
        cfg = config or OpenAIEmbedderConfig()
        super().__init__(cfg.batch_size)

        self._cfg = cfg
        self._model_max_tokens = _MODEL_MAX_TOKENS.get(cfg.model, _DEFAULT_MAX_TOKENS)
        self._enc = tiktoken.get_encoding(cfg.encoding_name)


        if client is not None:
            self._client = client
        else:
            try:
                from openai import OpenAI
                self._client = OpenAI()
            except ImportError as exc:
                raise ImportError(
                    "openai 包未安装，请执行：uv add openai"
                ) from exc
    
    @property
    def model_name(self) -> str:
        return self._cfg.model


    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        截断超长文本 + token 感知二次分批 + 重试
        """

        truncated = [self._truncate(t) for t in texts]

        token_batches = self._build_token_batches(truncated)

        all_vectors: list[list[float]] = []
        for batch in token_batches:
            all_vectors.extend(self._embed_batch_with_retry(batch))
        return all_vectors

    
    def _truncate(self, text: str) -> str:
        """单条文本超过模型上限被截断"""
        tokens = self._enc.encode(text)

        if len(tokens) <= self._model_max_tokens:
            return text
        logger.warning("文本超过模型 token 上限被截断")

        return self._enc.decode(tokens[:self._model_max_tokens])
    
    def _build_token_batches(self, texts: list[str]) -> list[list[str]]:
        """
        token 感知二次分批，当超过 _MAX_TOKENS_PER_REQUEST 被切分
        """
        batches: list[list[str]] = []
        current: list[str] = []
        current_tokens = 0

        for text in texts:
            token_count = len(self._enc.encode(text))
            if current and current_tokens + token_count > _MAX_TOKENS_PER_REQUEST:
                batches.append(current)
                current = []
                current_tokens = 0

            current.append(text)
            current_tokens += token_count

        if current:
            batches.append(current)
        
        return batches
    
    def _embed_batch_with_retry(self, texts: list[str]) -> list[list[float]]:
        """
        调用 OpenaAI Embedding API，遇到网络异常 / 429 / 5xx 进行指数退避重试
        """
        import openai

        last_exc: Exception | None = None
        for attempt in range(self._cfg.max_retries):
            is_last = attempt == self._cfg.max_retries - 1
            try:
                response = self._client.embeddings.create(
                    model=self._cfg.model,
                    input=texts
                )
                sorted_data = sorted(response.data, key=lambda d : d.index)
                return [d.embedding for d in sorted_data]
            except (openai.APIConnectionError, openai.RateLimitError, openai.APIStatusError) as exc:
                if (
                    isinstance(exc, openai.APIStatusError)
                    and not isinstance(exc, openai.RateLimitError)
                    and exc.status_code < 500
                ):
                    raise
                last_exc = exc
                if is_last:
                    break
                wait = self._cfg.retry_base_delay * (2 ** attempt)
                logger.warning(
                    "%s，%gs 后重试（%d/%d）",
                    type(exc).__name__, wait, attempt + 1, self._cfg.max_retries,
                )
                time.sleep(wait)
        raise RuntimeError(
            f"OpenAI Embedding 调用失败，已重试 {self._cfg.max_retries} 次"
        ) from last_exc
