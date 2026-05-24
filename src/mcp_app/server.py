"""FastMCP server assembly: tools + ASGI auth middleware + Starlette app."""
from __future__ import annotations

import logging
from functools import partial

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth.acl import AclResolver
from auth.jwt_builder import QdrantJWTBuilder, mint_service_token
from auth.oidc import OIDCValidator
from config import Settings

from .middleware import OIDCAuthMiddleware
from .tools import register_tools

logger = logging.getLogger(__name__)


def _build_components(
    settings: Settings,
) -> tuple[OIDCValidator, AclResolver, QdrantJWTBuilder, partial[str]]:
    """Construct validator, resolver, JWT builder, and service token factory."""
    validator = OIDCValidator(
        issuer_url=settings.oidc_issuer_url,
        audience=settings.oidc_audience,
        jwks_cache_ttl=settings.oidc_jwks_cache_ttl,
    )
    service_token_factory = partial(
        mint_service_token,
        settings.qdrant_jwt_secret,
        settings.rbac_service_token_ttl,
    )
    resolver = AclResolver(
        qdrant_url=settings.qdrant_url,
        acl_collection=settings.rbac_acl_collection,
        cache_ttl=settings.rbac_acl_cache_ttl,
        service_token_factory=service_token_factory,
    )
    builder = QdrantJWTBuilder(
        secret=settings.qdrant_jwt_secret,
        admin_role=settings.rbac_admin_role,
        ttl_seconds=settings.qdrant_jwt_ttl,
        resolver=resolver,
    )
    return validator, resolver, builder, service_token_factory


def create_mcp(
    settings: Settings,
    resolver: AclResolver,
    service_token_factory: partial[str],
) -> FastMCP:
    mcp: FastMCP = FastMCP(
        name="qdrant-rbac",
        instructions=(
            "MCP server exposing a Qdrant vector database. Every tool requires "
            "an OIDC bearer token in the Authorization header. Per-collection "
            "access is governed by grants stored in the ACL collection "
            f"'{settings.rbac_acl_collection}'; admins (members of the "
            f"'{settings.rbac_admin_role}' role) bypass the ACL and may manage "
            "grants via the list_acl / grant_access / revoke_access / "
            "refresh_acl tools."
        ),
    )
    register_tools(mcp, settings, resolver, service_token_factory)
    return mcp


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_app(settings: Settings) -> Starlette:
    """Build the ASGI app: MCP routes + auth middleware + /health."""
    validator, resolver, jwt_builder, service_token_factory = _build_components(settings)
    mcp = create_mcp(settings, resolver, service_token_factory)

    app: Starlette = mcp.http_app(path=settings.mcp_path, transport="streamable-http")
    app.add_route("/health", _health, methods=["GET"])
    app.add_middleware(
        OIDCAuthMiddleware,
        validator=validator,
        jwt_builder=jwt_builder,
    )
    return app
