"""Thin async wrappers around the Qdrant collection operations we expose."""
from __future__ import annotations

from typing import Any, cast

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import ExtendedPointId, Filter, PayloadSchemaType, PointStruct


async def search(
    client: AsyncQdrantClient,
    *,
    collection: str,
    vector: list[float],
    limit: int = 10,
    query_filter: dict[str, Any] | None = None,
    with_payload: bool = True,
) -> list[dict[str, Any]]:
    response = await client.query_points(
        collection_name=collection,
        query=vector,
        limit=limit,
        query_filter=Filter(**query_filter) if query_filter else None,
        with_payload=with_payload,
    )
    return [
        {"id": p.id, "score": p.score, "payload": p.payload, "version": p.version}
        for p in response.points
    ]


async def scroll(
    client: AsyncQdrantClient,
    *,
    collection: str,
    limit: int = 50,
    offset: str | int | None = None,
    query_filter: dict[str, Any] | None = None,
    with_payload: bool = True,
) -> tuple[list[dict[str, Any]], str | int | None]:
    points, next_offset = await client.scroll(
        collection_name=collection,
        limit=limit,
        offset=offset,
        scroll_filter=Filter(**query_filter) if query_filter else None,
        with_payload=with_payload,
        with_vectors=False,
    )
    results: list[dict[str, Any]] = [
        {"id": p.id, "payload": p.payload} for p in points
    ]
    # qdrant types the next offset as ExtendedPointId | None (incl. UUID); the
    # tool surface only ever round-trips str/int ids, so narrow at this seam.
    return results, cast("str | int | None", next_offset)


async def facet(
    client: AsyncQdrantClient,
    *,
    collection: str,
    key: str,
    facet_filter: dict[str, Any] | None = None,
    limit: int = 1000,
    exact: bool = True,
) -> list[dict[str, Any]]:
    response = await client.facet(
        collection_name=collection,
        key=key,
        facet_filter=Filter(**facet_filter) if facet_filter else None,
        limit=limit,
        exact=exact,
    )
    return [{"value": h.value, "count": h.count} for h in response.hits]


async def ensure_payload_index(
    client: AsyncQdrantClient,
    *,
    collection: str,
    field: str,
    schema: PayloadSchemaType = PayloadSchemaType.KEYWORD,
) -> None:
    """Create a payload index on ``field`` so it can be faceted/filtered.

    Idempotent: re-creating an existing index is a no-op on the Qdrant side.
    """
    await client.create_payload_index(
        collection_name=collection,
        field_name=field,
        field_schema=schema,
        wait=True,
    )


async def upsert(
    client: AsyncQdrantClient,
    *,
    collection: str,
    points: list[PointStruct],
) -> dict[str, Any]:
    result = await client.upsert(collection_name=collection, points=points, wait=True)
    return {"status": str(result.status), "operation_id": result.operation_id}


async def delete(
    client: AsyncQdrantClient,
    *,
    collection: str,
    point_ids: list[str | int],
) -> dict[str, Any]:
    result = await client.delete(
        collection_name=collection,
        points_selector=cast("list[ExtendedPointId]", point_ids),
        wait=True,
    )
    return {"status": str(result.status), "operation_id": result.operation_id}


async def get_info(client: AsyncQdrantClient, *, collection: str) -> dict[str, Any]:
    info = await client.get_collection(collection_name=collection)
    return {
        "status": str(info.status),
        "points_count": info.points_count,
        "indexed_vectors_count": info.indexed_vectors_count,
        "segments_count": info.segments_count,
        "config": info.config.model_dump() if info.config else None,
    }


async def list_collection_names(client: AsyncQdrantClient) -> list[str]:
    response = await client.get_collections()
    return [c.name for c in response.collections]
