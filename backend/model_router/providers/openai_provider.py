from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

def create_llm(
    model: str = "gpt-4o",
    max_tokens: int = 1000,
    temperature: float = 0.0,
    streaming: bool = True,
    max_retries: int = 2,
    request_timeout: float = 60.0
) -> ChatOpenAI:
    """
    创建 ChatOpenAI 实例
    """
    openai_api_key = os.environ.get("OPENAI_API_KEY") or None
    openai_base_url = os.environ.get("OPENAI_BASE_URL") or None
    return ChatOpenAI(
        model=model,
        api_key=openai_api_key,
        base_url=openai_base_url,
        max_tokens=max_tokens,
        temperature=temperature,
        streaming=streaming,
        max_retries=max_retries,
        request_timeout=request_timeout,
    )