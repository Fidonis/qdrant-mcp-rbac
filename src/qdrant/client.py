"""Per-request Qdrant client factory."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from qdrant_client import AsyncQdrantClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def qdrant_client(url: str, jwt_token: str) -> AsyncIterator[AsyncQdrantClient]:
    """Yield a Qdrant client bound to the caller's JWT.

    The Qdrant server accepts JWTs in place of the static API key, so we pass
    the derived token through the `api_key` parameter.
    """
    client = AsyncQdrantClient(url=url, api_key=jwt_token)
    try:
        yield client
    finally:
        # Never let a teardown failure mask the original exception (if any).
        try:
            await client.close()
        except Exception:
            logger.exception("Failed to close Qdrant client cleanly")
