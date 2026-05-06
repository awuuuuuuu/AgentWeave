"""
全局统一配置（唯一配置入口）

所有组件通过 `from config import settings` 使用，禁止在其他模块直接读取 os.environ。
字段按组件分节，AliasChoices 保持对旧 RAG_* / API_* 前缀的向后兼容。

env 文件：backend/.env
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent / ".env"
# 在 Settings 实例化前预加载，确保 os.environ 已填充（供仍使用 os.environ 的历史代码）
load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    # ── OpenAI ───────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    # ── DashScope（Reranker）────────────────────────────────────────────────
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com"

    # ── API Server ────────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(
        default=["http://localhost:3000"],
        validation_alias=AliasChoices("CORS_ORIGINS", "API_CORS_ORIGINS"),
    )
    jwt_secret_key: str = ""
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = ""

    # ── Redis ─────────────────────────────────────────────────────────────────
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_backend_url: str = "redis://localhost:6379/1"
    # 工具结果缓存（DB=2，与 Celery DB=0/1 隔离）
    tool_cache_redis_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TOOL_CACHE_REDIS_URL", "API_TOOL_CACHE_REDIS_URL"),
    )
    # 用户画像缓存（可复用 tool_cache_redis_url，或单独配置）
    memory_redis_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MEMORY_REDIS_URL", "API_MEMORY_REDIS_URL"),
    )

    # ── Milvus ────────────────────────────────────────────────────────────────
    milvus_uri: str = Field(
        default="http://localhost:19530",
        validation_alias=AliasChoices("MILVUS_URI", "RAG_MILVUS_URI"),
    )

    # ── MinIO ─────────────────────────────────────────────────────────────────
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "ragent"

    # ── Ingestion ─────────────────────────────────────────────────────────────
    unstructured_api_url: str = ""
    unstructured_api_key: str = ""

    # ── RAG Chain ─────────────────────────────────────────────────────────────
    llm_model: str = Field(
        default="gpt-4o",
        validation_alias=AliasChoices("LLM_MODEL", "RAG_LLM_MODEL"),
    )
    max_context_tokens: int = Field(
        default=6000,
        validation_alias=AliasChoices("MAX_CONTEXT_TOKENS", "RAG_MAX_CONTEXT_TOKENS"),
    )
    output_reserve_tokens: int = Field(
        default=1000,
        validation_alias=AliasChoices("OUTPUT_RESERVE_TOKENS", "RAG_OUTPUT_RESERVE_TOKENS"),
    )
    use_reranker: bool = Field(
        default=True,
        validation_alias=AliasChoices("USE_RERANKER", "RAG_USE_RERANKER"),
    )
    reranker_type: str = Field(
        default="dashscope",
        validation_alias=AliasChoices("RERANKER_TYPE", "RAG_RERANKER_TYPE"),
    )
    reranker_model: str = Field(
        default="qwen3-rerank",
        validation_alias=AliasChoices("RERANKER_MODEL", "RAG_RERANKER_MODEL"),
    )
    candidate_multiplier: int = Field(
        default=3,
        validation_alias=AliasChoices("CANDIDATE_MULTIPLIER", "RAG_CANDIDATE_MULTIPLIER"),
    )

    # ── Tools ─────────────────────────────────────────────────────────────────
    tavily_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TAVILY_API_KEY", "API_TAVILY_API_KEY"),
    )

    # ── Memory ────────────────────────────────────────────────────────────────
    memory_llm_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("MEMORY_LLM_MODEL", "API_MEMORY_LLM_MODEL"),
    )

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        extra="ignore",
        populate_by_name=True,
    )


# 全局单例：所有模块直接 `from config import settings`
settings = Settings()
