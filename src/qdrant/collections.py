"""Thin async wrappers around the Qdrant collection operations we expose."""
from __future__ import annotations

from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, PointStruct


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
    return results, next_offset


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
        points_selector=point_ids,
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
