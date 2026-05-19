"""ACL resolver: loads role grants from a dedicated Qdrant collection.

Each grant is stored as one point in the ACL collection (default ``_rbac_acl``)
with payload ``{role, collection, access}``. The resolver caches the resulting
mapping with a TTL and exposes ``invalidate()`` so admin mutations can force a
reload on the next request.

The resolver itself reads via a self-minted ``service token`` (HS256-signed
with ``access: "m"``). It never touches user-derived JWTs.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable

from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from qdrant.client import qdrant_client

from .models import AclEntry, CollectionAccess

logger = logging.getLogger(__name__)

# Stable namespace so point IDs are deterministic per (role, collection).
_ACL_NAMESPACE = uuid.UUID("8c9f3b0e-4a5d-4d0a-9f1e-7d6c5b2a1f00")
# Single dummy vector — ACL points are never queried by similarity.
_ACL_VECTOR: list[float] = [0.0]
# Page size for scrolling the ACL collection. Used by both the resolver
# (full load) and the list_acl admin tool. Large enough that even sizable
# ACLs (thousands of grants) finish in a handful of requests; small enough
# to keep payloads bounded.
ACL_SCROLL_PAGE_SIZE = 256
# Total attempts (initial + retries) before giving up on a transient 5xx
# from Qdrant during an ACL load. Backoff is 0.5s, 1.0s, 2.0s, ...
_LOAD_MAX_ATTEMPTS = 3
_LOAD_BACKOFF_BASE = 0.5


def acl_point_id(role: str, collection: str) -> str:
    """Deterministic UUID5 derived from (role, collection)."""
    return str(uuid.uuid5(_ACL_NAMESPACE, f"{role}|{collection}"))


class AclResolver:
    """TTL-cached view onto the ACL collection."""

    def __init__(
        self,
        qdrant_url: str,
        acl_collection: str,
        cache_ttl: int,
        service_token_factory: Callable[[], str],
    ) -> None:
        self._qdrant_url = qdrant_url
        self._acl_collection = acl_collection
        self._cache_ttl = cache_ttl
        self._service_token_factory = service_token_factory

        self._mapping: dict[str, list[CollectionAccess]] | None = None
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()
        # Bumped on each invalidate(). A _load() that started under generation
        # N and finishes under generation N+1 discards its (now stale) result
        # instead of poisoning the cache.
        self._generation: int = 0

    @property
    def acl_collection(self) -> str:
        return self._acl_collection

    async def get_mapping(self) -> dict[str, list[CollectionAccess]]:
        """Return ``role -> list[CollectionAccess]``, refreshing on TTL miss."""
        if self._is_fresh():
            assert self._mapping is not None
            return self._mapping
        async with self._lock:
            if self._is_fresh():
                assert self._mapping is not None
                return self._mapping
            snapshot = self._generation
            new_mapping = await self._load()
            if snapshot == self._generation:
                self._mapping = new_mapping
                self._fetched_at = time.monotonic()
            # else: invalidate() ran during _load(). The data we just loaded
            # may not include the admin's mutation, so leave the cache marked
            # stale; this caller still gets new_mapping, but the next request
            # will reload.
            return new_mapping

    async def invalidate(self) -> None:
        """Force the next ``get_mapping`` call to reload from Qdrant.

        Non-blocking even when a ``_load`` is in flight: the generation bump
        is detected after the in-flight load and causes its result to be
        dropped instead of cached.
        """
        self._generation += 1
        self._mapping = None
        self._fetched_at = 0.0

    async def ensure_collection(self) -> None:
        """Idempotently create the ACL collection if it doesn't exist yet.

        Admin code paths bypass :meth:`get_mapping` (and therefore the lazy
        bootstrap inside ``_load``). They must call this explicitly before
        writing to the ACL collection, otherwise the first admin on a fresh
        install gets a 404 from Qdrant.
        """
        token = self._service_token_factory()
        async with qdrant_client(self._qdrant_url, token) as client:
            await self._bootstrap_collection(client)

    def _is_fresh(self) -> bool:
        return (
            self._mapping is not None
            and (time.monotonic() - self._fetched_at) < self._cache_ttl
        )

    async def _load(self) -> dict[str, list[CollectionAccess]]:
        token = self._service_token_factory()
        async with qdrant_client(self._qdrant_url, token) as client:
            for attempt in range(_LOAD_MAX_ATTEMPTS):
                try:
                    entries = await self._scroll_all(client)
                    break
                except UnexpectedResponse as exc:
                    if exc.status_code == 404:
                        logger.info(
                            "ACL collection '%s' missing; bootstrapping it",
                            self._acl_collection,
                        )
                        await self._bootstrap_collection(client)
                        return {}
                    if exc.status_code >= 500 and attempt < _LOAD_MAX_ATTEMPTS - 1:
                        backoff = _LOAD_BACKOFF_BASE * (2 ** attempt)
                        logger.warning(
                            "ACL load attempt %d/%d failed (status %d); "
                            "retrying in %.1fs",
                            attempt + 1,
                            _LOAD_MAX_ATTEMPTS,
                            exc.status_code,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    raise

        mapping: dict[str, list[CollectionAccess]] = {}
        for entry in entries:
            mapping.setdefault(entry.role, []).append(
                CollectionAccess(collection=entry.collection, access=entry.access)
            )
        logger.debug(
            "Loaded %d ACL entries spanning %d roles", len(entries), len(mapping)
        )
        return mapping

    async def _scroll_all(self, client: AsyncQdrantClient) -> list[AclEntry]:
        result: list[AclEntry] = []
        next_offset: str | int | None = None
        while True:
            points, next_offset = await client.scroll(
                collection_name=self._acl_collection,
                limit=ACL_SCROLL_PAGE_SIZE,
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                try:
                    result.append(AclEntry(**payload))
                except (ValidationError, TypeError):
                    logger.warning(
                        "Skipping malformed ACL point id=%s payload=%s",
                        point.id,
                        list(payload.keys()),
                    )
            if next_offset is None:
                break
        return result

    async def _bootstrap_collection(self, client: AsyncQdrantClient) -> None:
        """Idempotently create the ACL collection and its 'role' index."""
        try:
            await client.create_collection(
                collection_name=self._acl_collection,
                vectors_config=VectorParams(size=1, distance=Distance.COSINE),
            )
        except UnexpectedResponse as exc:
            # Likely raced with another worker — ignore "already exists".
            if exc.status_code not in (400, 409):
                raise
        try:
            await client.create_payload_index(
                collection_name=self._acl_collection,
                field_name="role",
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except UnexpectedResponse as exc:
            if exc.status_code not in (400, 409):
                logger.warning(
                    "Failed to create payload index on 'role': %s", exc, exc_info=True
                )


def make_acl_point(entry: AclEntry) -> PointStruct:
    """Build the point used to upsert an ACL grant."""
    return PointStruct(
        id=acl_point_id(entry.role, entry.collection),
        vector=_ACL_VECTOR,
        payload=entry.model_dump(),
    )
