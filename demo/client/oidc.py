"""Keycloak / OIDC token retrieval for the demo client.

Hits the realm's discovery document, then exchanges credentials at the token
endpoint. Supports two grant types:

* ``password`` -- Resource Owner Password Credentials. Useful for demos and
  CLI tooling where the user types their Keycloak username/password directly.
  Requires the realm client to have *Direct Access Grants* enabled.
* ``client_credentials`` -- Service account flow. The token then represents
  the client itself, not a human user; useful for headless integration tests.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class OIDCError(RuntimeError):
    """Raised when the token endpoint refuses the request."""


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str | None
    expires_at: float  # epoch seconds

    @property
    def expires_in(self) -> int:
        return max(0, int(self.expires_at - time.time()))

    @property
    def is_expired(self) -> bool:
        # 10s safety margin so we don't ship a token that expires mid-request.
        return time.time() >= (self.expires_at - 10)


async def _discover_token_endpoint(issuer_url: str) -> str:
    """Fetch ``token_endpoint`` from the OIDC discovery document."""
    url = issuer_url.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(url)
    if resp.status_code != 200:
        raise OIDCError(
            f"OIDC discovery failed at {url}: HTTP {resp.status_code} {resp.text}"
        )
    doc = resp.json()
    endpoint = doc.get("token_endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        raise OIDCError(f"Discovery document at {url} has no token_endpoint")
    return endpoint


def _build_form(
    *,
    grant_type: str,
    client_id: str,
    client_secret: str,
    username: str,
    password: str,
    scope: str,
) -> dict[str, str]:
    form: dict[str, str] = {
        "grant_type": grant_type,
        "client_id": client_id,
        "scope": scope,
    }
    if client_secret:
        form["client_secret"] = client_secret
    if grant_type == "password":
        if not username or not password:
            raise OIDCError("password grant requires OIDC_USERNAME and OIDC_PASSWORD")
        form["username"] = username
        form["password"] = password
    elif grant_type == "client_credentials":
        if not client_secret:
            raise OIDCError(
                "client_credentials grant requires OIDC_CLIENT_SECRET (a service account)"
            )
    else:
        raise OIDCError(f"unsupported grant_type: {grant_type}")
    return form


async def fetch_token(
    *,
    issuer_url: str,
    client_id: str,
    client_secret: str = "",
    grant_type: str = "password",
    username: str = "",
    password: str = "",
    extra_scopes: str = "",
) -> TokenBundle:
    """Discover the token endpoint and exchange credentials for an access token."""
    token_endpoint = await _discover_token_endpoint(issuer_url)
    scope = "openid"
    if extra_scopes.strip():
        scope = "openid " + extra_scopes.strip()

    form = _build_form(
        grant_type=grant_type,
        client_id=client_id,
        client_secret=client_secret,
        username=username,
        password=password,
        scope=scope,
    )

    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            token_endpoint,
            data=form,
            headers={"Accept": "application/json"},
        )

    if resp.status_code != 200:
        raise OIDCError(
            f"Token request to {token_endpoint} failed "
            f"(HTTP {resp.status_code}): {resp.text}"
        )

    payload = resp.json()
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OIDCError(f"Token response missing access_token: {payload}")

    expires_in = int(payload.get("expires_in", 300))
    return TokenBundle(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_at=time.time() + expires_in,
    )
