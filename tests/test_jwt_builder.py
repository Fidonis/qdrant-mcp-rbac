"""Minting of the HS256 JWTs that Qdrant accepts."""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest

from auth.jwt_builder import QdrantJWTBuilder, mint_service_token
from auth.models import CollectionAccess, DocCondition, DocPolicy, OIDCClaims

# Long enough that PyJWT does not flag the HMAC key as too short for HS256.
SECRET = "test-secret-for-qdrant-jwt-0123456789"
ADMIN_ROLE = "qdrant-admin"
TTL = 900


def _decode(token: str) -> dict[str, Any]:
    return jwt.decode(token, SECRET, algorithms=["HS256"])


def _builder(mapping: dict[str, list[CollectionAccess]] | None = None) -> QdrantJWTBuilder:
    resolver = AsyncMock()
    resolver.get_mapping = AsyncMock(return_value=mapping or {})
    return QdrantJWTBuilder(SECRET, ADMIN_ROLE, TTL, resolver)


def _claims(*roles: str) -> OIDCClaims:
    return OIDCClaims(sub="user-1", realm_roles=list(roles))


def test_service_token_is_global_manage_and_short_lived() -> None:
    token = mint_service_token(SECRET, 300)
    payload = _decode(token)
    assert payload["access"] == "m"
    assert payload["exp"] == pytest.approx(time.time() + 300, abs=5)
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_token_is_rejected_under_a_different_secret() -> None:
    token = mint_service_token(SECRET, 300)
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "a-completely-different-secret-0123456789", algorithms=["HS256"])


async def test_admin_role_gets_global_manage_without_consulting_the_acl() -> None:
    builder = _builder()
    result = await builder.build(_claims(ADMIN_ROLE))
    assert result.has_global_manage is True
    assert result.access_rules == []
    assert _decode(result.token)["access"] == "m"
    builder._resolver.get_mapping.assert_not_awaited()  # type: ignore[attr-defined]


async def test_manage_grant_in_the_acl_gets_global_manage() -> None:
    mapping = {"ops": [CollectionAccess(collection="*", access="m")]}
    result = await _builder(mapping).build(_claims("ops"))
    assert result.has_global_manage is True
    assert _decode(result.token)["access"] == "m"


async def test_grants_become_per_collection_rules() -> None:
    mapping = {
        "reader": [CollectionAccess(collection="docs", access="r")],
        "writer": [
            CollectionAccess(collection="docs", access="rw"),
            CollectionAccess(collection="notes", access="r"),
        ],
    }
    result = await _builder(mapping).build(_claims("reader", "writer"))

    assert result.has_global_manage is False
    # The most permissive level wins per collection.
    assert [(r.collection, r.access) for r in result.access_rules] == [
        ("docs", "rw"),
        ("notes", "r"),
    ]
    payload = _decode(result.token)
    assert payload["access"] == [
        {"collection": "docs", "access": "rw"},
        {"collection": "notes", "access": "r"},
    ]
    assert payload["exp"] == pytest.approx(time.time() + TTL, abs=5)


async def test_doc_policy_stays_server_side() -> None:
    policy = DocPolicy(
        default="deny",
        conditions=[DocCondition(field="dept", mode="allow", values=["finance"])],
    )
    mapping = {"fin": [CollectionAccess(collection="docs", access="r", doc_policy=policy)]}
    result = await _builder(mapping).build(_claims("fin"))

    assert result.access_rules[0].doc_policy == policy
    # Qdrant only needs collection and access; the filter is applied by this server.
    assert _decode(result.token)["access"] == [{"collection": "docs", "access": "r"}]


async def test_user_without_matching_roles_gets_no_access() -> None:
    mapping = {"reader": [CollectionAccess(collection="docs", access="r")]}
    result = await _builder(mapping).build(_claims("unrelated"))
    assert result.has_global_manage is False
    assert result.access_rules == []
    assert _decode(result.token)["access"] == []
