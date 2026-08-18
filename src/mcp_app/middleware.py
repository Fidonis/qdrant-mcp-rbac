"""ASGI middleware: validate OIDC bearer, derive Qdrant JWT, attach to scope.

We implement this as a low-level ASGI middleware (instead of Starlette's
`BaseHTTPMiddleware`) because the streamable-HTTP transport sends streaming
responses, and `BaseHTTPMiddleware` would buffer them.
"""
from __future__ import annotations

import json
import logging
from typing import Any, cast

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from auth.jwt_builder import QdrantJWTBuilder
from auth.oidc import InvalidTokenError, OIDCValidator

logger = logging.getLogger(__name__)

# Scope-state keys. The scope[\"state\"] dict is also exposed via Starlette's
# ``request.state``, so tools can read these via ``get_http_request().state``.
STATE_CLAIMS = "oidc_claims"
STATE_QDRANT_TOKEN = "qdrant_token"

_PUBLIC_PATHS = frozenset({"/health", "/healthz"})


class OIDCAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        validator: OIDCValidator,
        jwt_builder: QdrantJWTBuilder,
    ) -> None:
        self.app = app
        self._validator = validator
        self._jwt_builder = jwt_builder

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        auth_header = _get_header(scope, b"authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            await _respond_json(send, 401, {"error": "missing_bearer_token"})
            return

        token = auth_header[7:].strip()
        try:
            claims = await self._validator.validate(token)
        except InvalidTokenError as exc:
            logger.info("OIDC validation failed: %s", exc)
            await _respond_json(send, 401, {"error": "invalid_token"})
            return
        except Exception:
            logger.exception("Unexpected error during OIDC validation")
            await _respond_json(send, 500, {"error": "auth_internal_error"})
            return

        try:
            qdrant_token = await self._jwt_builder.build(claims)
        except Exception:
            logger.exception("Failed to build Qdrant JWT for sub=%s", claims.sub)
            await _respond_json(send, 500, {"error": "auth_internal_error"})
            return

        if not qdrant_token.has_global_manage and not qdrant_token.access_rules:
            logger.info(
                "Rejecting authenticated user without any mapped roles: sub=%s",
                claims.sub,
            )
            await _respond_json(send, 403, {"error": "no_mapped_roles"})
            return

        # Starlette initializes scope["state"] as a dict on first access via
        # Request.state. Pre-populating it here is equivalent.
        state = scope.setdefault("state", {})
        state[STATE_CLAIMS] = claims
        state[STATE_QDRANT_TOKEN] = qdrant_token

        await self.app(scope, receive, send)


def _get_header(scope: Scope, name: bytes) -> str | None:
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() == name:
            return cast(str, raw_value.decode("latin-1"))
    return None


async def _respond_json(send: Send, status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode("utf-8")
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode("ascii")),
    ]
    if status == 401:
        headers.append((b"www-authenticate", b'Bearer realm="mcp", error="invalid_token"'))
    start: Message = {
        "type": "http.response.start",
        "status": status,
        "headers": headers,
    }
    await send(start)
    body_msg: Message = {"type": "http.response.body", "body": payload}
    await send(body_msg)
