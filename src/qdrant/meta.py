"""Helpers for the ``_collection_meta`` system collection.

Each data collection gets exactly one point in this collection describing
the embedding configuration used at ingestion time:

    {"collection": "<name>", "embedding_model": "<model>", "vector_dimension": <int>}

The MCP server reads this entry before embedding a text query so it can use
the same model that produced the collection's vectors. Bootstrap writes it
after vectorizing a collection.

The point format mirrors ``_rbac_acl``:

* Deterministic UUID5 id derived from the collection name (idempotent upserts).
* A dummy single-element vector — these points are never queried by similarity.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import Distance, PointStruct, VectorParams

logger = logging.getLogger(__name__)

# Stable namespace so point IDs are deterministic per collection name.
META_NAMESPACE = uuid.UUID("9e3a5c2f-8b7d-4f1e-a6b3-2d8c9e4f1a02")
# Single dummy vector — meta points are never queried by similarity.
META_VECTOR: list[float] = [0.0]


def meta_point_id(collection_name: str) -> str:
    """Deterministic UUID5 derived from the data collection name."""
    return str(uuid.uuid5(META_NAMESPACE, collection_name))


def make_meta_point(
    collection_name: str, *, embedding_model: str, vector_dimension: int
) -> PointStruct:
    """Build the point used to upsert the meta entry for ``collection_name``."""
    return PointStruct(
        id=meta_point_id(collection_name),
        vector=META_VECTOR,
        payload={
            "collection": collection_name,
            "embedding_model": embedding_model,
            "vector_dimension": vector_dimension,
        },
    )


async def ensure_meta_collection(
    client: AsyncQdrantClient, *, meta_collection: str
) -> None:
    """Idempotently create the meta collection if it doesn't exist yet."""
    try:
        await client.create_collection(
            collection_name=meta_collection,
            vectors_config=VectorParams(size=1, distance=Distance.COSINE),
        )
    except UnexpectedResponse as exc:
        # Likely raced with another worker — ignore "already exists".
        if exc.status_code not in (400, 409):
            raise


async def read_meta(
    client: AsyncQdrantClient,
    *,
    meta_collection: str,
    collection_name: str,
) -> dict[str, Any] | None:
    """Return the meta payload for ``collection_name`` or ``None`` if absent."""
    point_id = meta_point_id(collection_name)
    try:
        points = await client.retrieve(
            collection_name=meta_collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
    except UnexpectedResponse as exc:
        if exc.status_code == 404:
            return None
        raise
    if not points:
        return None
    payload = points[0].payload or {}
    return dict(payload) if payload else None
