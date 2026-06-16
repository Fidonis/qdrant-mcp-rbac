"""Unit tests for the ``qcoll.facet`` wrapper backing the list_documents tool."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from qdrant_client.models import Filter, PayloadSchemaType

from qdrant import collections as qcoll


def _hit(value: str | int, count: int) -> SimpleNamespace:
    # Mimic the fields of a Qdrant FacetValueHit the wrapper reads.
    return SimpleNamespace(value=value, count=count)


def _client(hits: list[Any]) -> AsyncMock:
    client = AsyncMock()
    client.facet = AsyncMock(return_value=SimpleNamespace(hits=hits))
    return client


async def test_facet_shapes_hits() -> None:
    client = _client([_hit("finance-2026.md", 5), _hit("finance-2025.md", 4)])
    result = await qcoll.facet(client, collection="finance", key="source")
    assert result == [
        {"value": "finance-2026.md", "count": 5},
        {"value": "finance-2025.md", "count": 4},
    ]


async def test_facet_forwards_key_limit_and_exact() -> None:
    client = _client([])
    await qcoll.facet(client, collection="finance", key="source", limit=250)
    client.facet.assert_awaited_once()
    kwargs = client.facet.call_args.kwargs
    assert kwargs["collection_name"] == "finance"
    assert kwargs["key"] == "source"
    assert kwargs["limit"] == 250
    # Exact counts by default — an inventory must not under/over-count chunks.
    assert kwargs["exact"] is True
    assert kwargs["facet_filter"] is None


async def test_facet_wraps_filter() -> None:
    client = _client([])
    flt = {"must": [{"key": "acl_tags", "match": {"value": "public"}}]}
    await qcoll.facet(client, collection="finance", key="source", facet_filter=flt)
    sent = client.facet.call_args.kwargs["facet_filter"]
    assert isinstance(sent, Filter)
    assert sent == Filter(**flt)


async def test_facet_empty() -> None:
    client = _client([])
    assert await qcoll.facet(client, collection="empty", key="source") == []


async def test_ensure_payload_index_creates_keyword_index() -> None:
    client = AsyncMock()
    client.create_payload_index = AsyncMock()
    await qcoll.ensure_payload_index(client, collection="finance", field="source")
    client.create_payload_index.assert_awaited_once()
    kwargs = client.create_payload_index.call_args.kwargs
    assert kwargs["collection_name"] == "finance"
    assert kwargs["field_name"] == "source"
    assert kwargs["field_schema"] == PayloadSchemaType.KEYWORD
    assert kwargs["wait"] is True
