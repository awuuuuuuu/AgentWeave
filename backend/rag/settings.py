from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent / ".env"


class RAGChainSettings(BaseSettings):
    llm_model: str = "gpt-4o"
    max_context_tokens: int = 6000
    output_reserve_tokens: int = 1000

    # 检索相关配置
    use_reranker: bool = True
    reranker_type: str = "dashscope"          # "dashscope" | "local"
    reranker_model: str = Field(
        default="qwen3-rerank", 
        validation_alias=AliasChoices("RAG_RERANKER_MODEL", "RERANKER_MODEL")
    )
    dashscope_api_key: str = Field(
        default="", 
        validation_alias=AliasChoices("RAG_DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY")
    )
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com", 
        validation_alias=AliasChoices("RAG_DASHSCOPE_BASE_URL", "DASHSCOPE_BASE_URL")
    )
    candidate_multiplier: int = 3   # HybridRetriever fetch_k = top_k * multiplier


    # 基础设施配置：优先读 RAG_MILVUS_URI，兼容无前缀的 MILVUS_URI
    milvus_uri: str = Field(
        default="http://localhost:19530",
        validation_alias=AliasChoices("RAG_MILVUS_URI", "MILVUS_URI"),
    )

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_prefix="RAG_",
        extra="ignore",
        populate_by_name=True,
    )