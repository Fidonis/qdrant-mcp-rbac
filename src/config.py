"""Application settings loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_SRC_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_SRC_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # OIDC / Keycloak
    oidc_issuer_url: str = Field(min_length=1)
    oidc_audience: str = Field(min_length=1)
    oidc_jwks_cache_ttl: int = Field(default=3600, ge=0)

    # Qdrant
    qdrant_url: str = Field(min_length=1)
    qdrant_jwt_secret: str = Field(min_length=1)
    qdrant_jwt_ttl: int = Field(default=3600, ge=60)

    # MCP server
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
    mcp_path: str = "/mcp"

    # Logging
    log_level: str = "INFO"

    # Embeddings — OpenAI-compatible endpoint used to vectorize text queries.
    # The model itself is not configured globally; it's looked up per collection
    # in the ``embedding_meta_collection`` system collection, which bootstrap
    # populates at ingestion time.
    embedding_api_url: str = "http://localhost:11434/v1"
    embedding_api_key: str = ""
    embedding_meta_collection: str = "_collection_meta"

    # RBAC
    # Break-glass admin role that bypasses the ACL lookup. Members of this OIDC
    # role always receive a global-manage Qdrant JWT and can therefore bootstrap
    # / repair the ACL collection.
    rbac_admin_role: str = "qdrant-admin"
    # Name of the Qdrant collection holding role-grants. Auto-created on first
    # access by the AclResolver using a self-minted service token.
    rbac_acl_collection: str = "_rbac_acl"
    # TTL for the in-memory ACL cache in seconds. Admin mutations call
    # ``invalidate()`` on the cache directly so this only affects out-of-band
    # changes (e.g. someone editing the ACL collection via Qdrant directly).
    # A TTL of 0 would degenerate to a Qdrant scroll on every authenticated
    # request, so the minimum is 1 second.
    rbac_acl_cache_ttl: int = Field(default=60, ge=1)
    # TTL for service tokens minted by the server itself for ACL reads.
    # Must comfortably outlast a full ACL scroll under load, otherwise the
    # token can expire mid-load and the in-flight scroll will fail with 401.
    rbac_service_token_ttl: int = Field(default=300, ge=10)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached singleton settings instance."""
    return Settings()  # type: ignore[call-arg]
