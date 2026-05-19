"""Client-demo settings loaded from .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # OIDC
    oidc_issuer_url: str = Field(min_length=1)
    oidc_client_id: str = Field(min_length=1)
    oidc_client_secret: str = ""
    oidc_grant_type: Literal["password", "client_credentials"] = "password"
    oidc_username: str = ""
    oidc_password: str = ""
    oidc_scopes: str = ""

    # MCP
    mcp_server_url: str = Field(min_length=1)

    # LLM (via litellm)
    llm_model: str = Field(min_length=1, default="gpt-4o-mini")
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_temperature: float = 0.2
    llm_max_iterations: int = Field(default=8, ge=1, le=50)
    llm_system_prompt: str = ""

    # Logging
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
