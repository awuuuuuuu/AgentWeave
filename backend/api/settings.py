from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent / ".env"

class APISettings(BaseSettings):
    cors_origins: list[str] = ["http://localhost:3000"]
    
    tavily_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TAVILY_API_KEY", "API_TAVILY_API_KEY")
    )
    tool_cache_redis_url: str | None = None

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_prefix="API_",
        extra="ignore",
        populate_by_name=True,
    )