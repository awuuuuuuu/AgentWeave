from __future__ import annotations

from langchain_openai import ChatOpenAI

from config import settings

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
    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key or None,
        base_url=settings.openai_base_url or None,
        max_tokens=max_tokens,
        temperature=temperature,
        streaming=streaming,
        max_retries=max_retries,
        request_timeout=request_timeout,
    )