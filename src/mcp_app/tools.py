"""MCP tool definitions backed by Qdrant operations."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from pydantic import ValidationError
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

from auth.acl import ACL_SCROLL_PAGE_SIZE, AclResolver, acl_point_id, make_acl_point
from auth.doc_filter import DENY_ALL, build_doc_filter, combine_with_user_filter
from auth.models import (
    AclEntry,
    CollectionAccess,
    DocPolicy,
    OIDCClaims,
    QdrantAccessLevel,
    QdrantToken,
    access_satisfies,
)
from config import Settings
from qdrant import collections as qcoll
from qdrant import meta as qmeta
from qdrant.client import qdrant_client

from .middleware import STATE_CLAIMS, STATE_QDRANT_TOKEN

logger = logging.getLogger(__name__)

# Timeout for outbound calls to the embedding endpoint. Generous enough to
# tolerate a cold start on a self-hosted model, but bounded so a hung
# provider can't pin a tool call indefinitely.
_EMBEDDING_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    resolver: AclResolver,
    service_token_factory: Callable[[], str],
) -> None:
    """Attach all Qdrant- and ACL-backed tools to the FastMCP server."""

    qdrant_url = settings.qdrant_url
    acl_collection = resolver.acl_collection
    meta_collection = settings.embedding_meta_collection
    embedding_api_url = settings.embedding_api_url.rstrip("/")
    embedding_api_key = settings.embedding_api_key
    # Collections owned by the server itself — hidden from listings and
    # rejected by the generic mutation tools.
    system_collections = frozenset({acl_collection, meta_collection})

    # -------------------------- data tools --------------------------

    @mcp.tool
    async def list_collections() -> dict[str, Any]:
        """List Qdrant collections the caller has at least read access to.

        Internal system collections (ACL grants, embedding metadata) are
        hidden from this listing — they are managed by dedicated tools or
        by the bootstrap process.
        """
        token = _require_qdrant_token()
        if token.has_global_manage:
            async with qdrant_client(qdrant_url, token.token) as client:
                names = await qcoll.list_collection_names(client)
            return {"collections": [n for n in names if n not in system_collections]}
        return {
            "collections": sorted(
                {rule.collection for rule in token.access_rules} - system_collections
            )
        }

    @mcp.tool
    async def get_collection_info(collection: str) -> dict[str, Any]:
        """Return metadata for a single collection. Requires read access."""
        token = _require_access(collection, "r")
        async with qdrant_client(qdrant_url, token.token) as client:
            return await qcoll.get_info(client, collection=collection)

    @mcp.tool
    async def search_collection(
        collection: str,
        vector: list[float],
        limit: int = 10,
        query_filter: dict[str, Any] | None = None,
        with_payload: bool = True,
    ) -> dict[str, Any]:
        """Run a vector similarity search. Requires read access."""
        if limit <= 0 or limit > 1000:
            raise ToolError("limit must be between 1 and 1000")
        if not vector:
            raise ToolError("vector must be a non-empty list of floats")
        token = _require_access(collection, "r")
        effective_filter, deny_all = _apply_doc_policy(token, collection, query_filter)
        if deny_all:
            return {"results": [], "count": 0}
        async with qdrant_client(qdrant_url, token.token) as client:
            results = await qcoll.search(
                client,
                collection=collection,
                vector=vector,
                limit=limit,
                query_filter=effective_filter,
                with_payload=with_payload,
            )
        return {"results": results, "count": len(results)}

    @mcp.tool
    async def search_collection_by_text(
        collection: str,
        query: str,
        limit: int = 10,
        query_filter: dict[str, Any] | None = None,
        with_payload: bool = True,
    ) -> dict[str, Any]:
        """Search a collection using a natural-language text query.

        The query is embedded via the server's configured OpenAI-compatible
        embeddings endpoint using the model that was used to vectorize this
        collection (looked up in the meta collection). Requires read access.
        """
        if not query.strip():
            raise ToolError("query must be a non-empty string")
        if limit <= 0 or limit > 1000:
            raise ToolError("limit must be between 1 and 1000")
        token = _require_access(collection, "r")

        meta = await _fetch_collection_meta(collection)
        model_name = meta.get("embedding_model")
        if not isinstance(model_name, str) or not model_name:
            raise ToolError(
                f"collection '{collection}' has no embedding_model recorded in "
                f"'{meta_collection}' — re-run bootstrap to populate it"
            )

        vector = await _embed_text(query, model=model_name)
        expected_dim = meta.get("vector_dimension")
        if isinstance(expected_dim, int) and expected_dim != len(vector):
            raise ToolError(
                f"embedding endpoint returned a vector of size {len(vector)} but "
                f"collection '{collection}' was indexed with size {expected_dim}; "
                f"check that EMBEDDING_API_URL serves model '{model_name}'"
            )

        effective_filter, deny_all = _apply_doc_policy(token, collection, query_filter)
        if deny_all:
            return {"results": [], "count": 0}

        async with qdrant_client(qdrant_url, token.token) as client:
            results = await qcoll.search(
                client,
                collection=collection,
                vector=vector,
                limit=limit,
                query_filter=effective_filter,
                with_payload=with_payload,
            )
        return {"results": results, "count": len(results)}

    @mcp.tool
    async def upsert_points(
        collection: str,
        points: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Insert or update points. Requires read-write access.

        Each point must contain ``id``, ``vector``, and optionally ``payload``.
        """
        if not points:
            raise ToolError("points must be a non-empty list")
        if collection in system_collections:
            raise ToolError(
                f"the system collection '{collection}' must not be modified "
                "through this tool"
            )
        try:
            structs = [PointStruct(**p) for p in points]
        except (ValidationError, TypeError) as exc:
            logger.info("Rejected upsert with invalid points: %s", exc.__class__.__name__)
            raise ToolError(
                "invalid point format: each point requires 'id' and 'vector', "
                "optionally 'payload'"
            ) from None
        token = _require_access(collection, "rw")
        async with qdrant_client(qdrant_url, token.token) as client:
            return await qcoll.upsert(client, collection=collection, points=structs)

    @mcp.tool
    async def delete_points(
        collection: str,
        point_ids: list[str | int],
    ) -> dict[str, Any]:
        """Delete points by id. Requires read-write access."""
        if not point_ids:
            raise ToolError("point_ids must be a non-empty list")
        if collection in system_collections:
            raise ToolError(
                f"the system collection '{collection}' must not be modified "
                "through this tool"
            )
        token = _require_access(collection, "rw")
        async with qdrant_client(qdrant_url, token.token) as client:
            return await qcoll.delete(
                client, collection=collection, point_ids=point_ids
            )

    # --------------------------- ACL tools --------------------------

    @mcp.tool
    async def list_acl(role: str | None = None) -> dict[str, Any]:
        """List role grants stored in the ACL collection. Admin only.

        Optionally filter by ``role``.
        """
        token = _require_admin()
        flt: Filter | None = None
        if role is not None:
            flt = Filter(
                must=[FieldCondition(key="role", match=MatchValue(value=role))]
            )
        entries: list[dict[str, Any]] = []
        async with qdrant_client(qdrant_url, token.token) as client:
            try:
                next_offset: str | int | None = None
                while True:
                    points, next_offset = await client.scroll(
                        collection_name=acl_collection,
                        limit=ACL_SCROLL_PAGE_SIZE,
                        offset=next_offset,
                        scroll_filter=flt,
                        with_payload=True,
                        with_vectors=False,
                    )
                    for point in points:
                        if point.payload:
                            entries.append(dict(point.payload))
                    if next_offset is None:
                        break
            except UnexpectedResponse as exc:
                if exc.status_code == 404:
                    return {"entries": [], "count": 0, "note": "acl_collection_missing"}
                raise
        return {"entries": entries, "count": len(entries)}

    @mcp.tool
    async def grant_access(
        role: str,
        collection: str,
        access: QdrantAccessLevel,
        doc_policy: DocPolicy | None = None,
    ) -> dict[str, Any]:
        """Grant ``role`` the given ``access`` on ``collection``. Admin only.

        Idempotent — the (role, collection) pair maps to a deterministic
        point id, so subsequent calls update the existing grant in place.
        For global manage grants pass ``collection='*'`` and ``access='m'``.

        Optional ``doc_policy`` restricts which documents the role can see in
        ``collection``. The server translates it into a Qdrant payload filter
        injected into every search.

        ``doc_policy`` shape::

            {
              "default": "allow" | "deny",
              "conditions": [
                { "field": "<payload field>", "mode": "allow" | "deny", "values": ["<v1>", ...] }
              ]
            }

        Common patterns:

        * Allow only documents whose ``source`` field matches a filename::

            {"default": "deny", "conditions": [
              {"field": "source", "mode": "allow", "values": ["finance-2025.md"]}
            ]}

        * Hide documents tagged ``confidential`` from a role::

            {"default": "allow", "conditions": [
              {"field": "acl_tags", "mode": "deny", "values": ["confidential"]}
            ]}

        * Allow only documents tagged ``public``::

            {"default": "deny", "conditions": [
              {"field": "acl_tags", "mode": "allow", "values": ["public"]}
            ]}

        Omit ``doc_policy`` (or pass ``null``) to grant access to all documents.
        """
        token = _require_admin()
        try:
            entry = AclEntry(
                role=role,
                collection=collection,
                access=access,
                doc_policy=doc_policy,
            )
        except ValidationError as exc:
            logger.info("Rejected grant_access with invalid args: %s", exc.__class__.__name__)
            raise ToolError(
                "invalid arguments: role and collection must be non-empty strings, "
                "access must be one of 'r', 'rw', 'm', and doc_policy (if given) "
                "must match the documented schema"
            ) from None
        # Admin path bypasses the lazy bootstrap in AclResolver._load(), so on
        # a fresh install the ACL collection won't exist yet. Ensure it.
        await resolver.ensure_collection()
        async with qdrant_client(qdrant_url, token.token) as client:
            await client.upsert(
                collection_name=acl_collection,
                points=[make_acl_point(entry)],
                wait=True,
            )
        await resolver.invalidate()
        claims = _require_claims()
        logger.warning(
            "ACL.grant sub=%s role=%s collection=%s access=%s",
            claims.sub,
            role,
            collection,
            access,
        )
        return {"granted": entry.model_dump()}

    @mcp.tool
    async def revoke_access(role: str, collection: str) -> dict[str, Any]:
        """Revoke a grant for ``(role, collection)``. Admin only.

        Idempotent: returns ``{"removed": true}`` whenever no such grant
        exists after the call, whether or not one existed beforehand.
        If the ACL collection itself doesn't exist, returns
        ``{"removed": false}``.
        """
        token = _require_admin()
        if (
            not isinstance(role, str)
            or not role.strip()
            or not isinstance(collection, str)
            or not collection.strip()
        ):
            raise ToolError("role and collection must be non-empty strings")
        point_id = acl_point_id(role, collection)
        async with qdrant_client(qdrant_url, token.token) as client:
            try:
                await client.delete(
                    collection_name=acl_collection,
                    points_selector=[point_id],
                    wait=True,
                )
            except UnexpectedResponse as exc:
                if exc.status_code == 404:
                    return {"removed": False}
                raise
        await resolver.invalidate()
        claims = _require_claims()
        logger.warning(
            "ACL.revoke sub=%s role=%s collection=%s",
            claims.sub,
            role,
            collection,
        )
        return {"removed": True}

    @mcp.tool
    async def refresh_acl() -> dict[str, Any]:
        """Force a reload of the in-memory ACL cache. Admin only."""
        _require_admin()
        await resolver.invalidate()
        return {"status": "invalidated"}

    # --------------------- helpers (scoped to closure) --------------------

    async def _fetch_collection_meta(collection: str) -> dict[str, Any]:
        """Look up the meta entry for ``collection`` via a service token."""
        token = service_token_factory()
        try:
            async with qdrant_client(qdrant_url, token) as client:
                meta = await qmeta.read_meta(
                    client,
                    meta_collection=meta_collection,
                    collection_name=collection,
                )
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                raise ToolError(
                    f"meta collection '{meta_collection}' is missing — "
                    "run bootstrap to populate it"
                ) from None
            raise
        if meta is None:
            raise ToolError(
                f"no embedding metadata for collection '{collection}' in "
                f"'{meta_collection}' — re-run bootstrap to populate it"
            )
        return meta

    async def _embed_text(query: str, *, model: str) -> list[float]:
        """POST to the OpenAI-compatible embeddings endpoint."""
        headers = {"Content-Type": "application/json"}
        if embedding_api_key:
            headers["Authorization"] = f"Bearer {embedding_api_key}"
        payload = {"model": model, "input": query}
        url = f"{embedding_api_url}/embeddings"
        try:
            async with httpx.AsyncClient(timeout=_EMBEDDING_HTTP_TIMEOUT) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            logger.info("Embedding endpoint unreachable: %s", exc.__class__.__name__)
            raise ToolError(
                f"embedding endpoint {url} unreachable: {exc.__class__.__name__}"
            ) from None
        if response.status_code >= 400:
            logger.info(
                "Embedding endpoint returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            raise ToolError(
                f"embedding endpoint returned status {response.status_code}"
            )
        try:
            data = response.json()
            embedding = data["data"][0]["embedding"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ToolError("embedding endpoint returned an unexpected payload") from None
        if not isinstance(embedding, list) or not embedding:
            raise ToolError("embedding endpoint returned an empty vector")
        return [float(x) for x in embedding]


# ---------------------------- helpers ----------------------------


def _require_qdrant_token() -> QdrantToken:
    """Fetch the per-request Qdrant token put in place by the auth middleware."""
    request = get_http_request()
    token = getattr(request.state, STATE_QDRANT_TOKEN, None)
    if not isinstance(token, QdrantToken):
        logger.error("Tool invoked without a Qdrant token in scope state")
        raise ToolError("authentication required")
    return token


def _require_claims() -> OIDCClaims:
    request = get_http_request()
    claims = getattr(request.state, STATE_CLAIMS, None)
    if not isinstance(claims, OIDCClaims):
        raise ToolError("authentication required")
    return claims


def _require_access(collection: str, required: QdrantAccessLevel) -> QdrantToken:
    token = _require_qdrant_token()
    if token.has_global_manage:
        return token
    rule = _find_rule(token.access_rules, collection)
    if rule is None or not access_satisfies(rule.access, required):
        claims = _require_claims()
        logger.warning(
            "ACL.deny sub=%s collection=%s required=%s granted=%s",
            claims.sub,
            collection,
            required,
            rule.access if rule else None,
        )
        raise ToolError(
            f"forbidden: '{required}' access on collection '{collection}' is required"
        )
    return token


def _require_admin() -> QdrantToken:
    token = _require_qdrant_token()
    if not token.has_global_manage:
        claims = _require_claims()
        logger.warning("ACL.admin_deny sub=%s", claims.sub)
        raise ToolError("forbidden: admin (global manage) access is required")
    return token


def _find_rule(
    rules: list[CollectionAccess], collection: str
) -> CollectionAccess | None:
    for rule in rules:
        if rule.collection == collection:
            return rule
    return None


def _apply_doc_policy(
    token: QdrantToken,
    collection: str,
    user_filter: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, bool]:
    """Combine the caller-supplied filter with the doc policy for ``collection``.

    Returns ``(effective_filter, deny_all)``. When ``deny_all`` is true the
    caller MUST short-circuit and return an empty result set without
    issuing the Qdrant query.
    """
    if token.has_global_manage:
        return user_filter, False
    rule = _find_rule(token.access_rules, collection)
    if rule is None or rule.doc_policy is None:
        return user_filter, False
    doc_filter = build_doc_filter(rule.doc_policy)
    if doc_filter is DENY_ALL:
        return None, True
    return combine_with_user_filter(doc_filter, user_filter), False
