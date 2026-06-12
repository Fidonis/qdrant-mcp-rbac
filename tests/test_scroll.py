"""Unit tests for the ``qcoll.scroll`` wrapper backing the scroll_collection tool."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from qdrant_client.models import Filter

from qdrant import collections as qcoll


def _record(point_id: str | int, payload: dict[str, Any] | None) -> SimpleNamespace:
    # Mimic the only fields of a Qdrant Record the wrapper reads.
    return SimpleNamespace(id=point_id, payload=payload)


def _client(points: list[Any], next_offset: str | int | None) -> AsyncMock:
    client = AsyncMock()
    client.scroll = AsyncMock(return_value=(points, next_offset))
    return client


async def test_scroll_shapes_records_and_returns_offset() -> None:
    client = _client(
        [_record("a", {"source": "finance-2025.md"}), _record(7, None)],
        "next-cursor",
    )
    results, next_offset = await qcoll.scroll(client, collection="finance")
    assert results == [
        {"id": "a", "payload": {"source": "finance-2025.md"}},
        {"id": 7, "payload": None},
    ]
    assert next_offset == "next-cursor"


async def test_scroll_forwards_paging_and_vector_flags() -> None:
    client = _client([], None)
    await qcoll.scroll(
        client,
        collection="finance",
        limit=25,
        offset="cur",
        with_payload=False,
    )
    client.scroll.assert_awaited_once()
    kwargs = client.scroll.call_args.kwargs
    assert kwargs["collection_name"] == "finance"
    assert kwargs["limit"] == 25
    assert kwargs["offset"] == "cur"
    assert kwargs["with_payload"] is False
    # Vectors are never returned — payload-only is what callers list on.
    assert kwargs["with_vectors"] is False
    assert kwargs["scroll_filter"] is None


async def test_scroll_wraps_query_filter() -> None:
    client = _client([], None)
    flt = {"must": [{"key": "source", "match": {"value": "finance-2025.md"}}]}
    await qcoll.scroll(client, collection="finance", query_filter=flt)
    sent = client.scroll.call_args.kwargs["scroll_filter"]
    assert isinstance(sent, Filter)
    assert sent == Filter(**flt)


async def test_scroll_empty_collection() -> None:
    client = _client([], None)
    results, next_offset = await qcoll.scroll(client, collection="empty")
    assert results == []
    assert next_offset is None
