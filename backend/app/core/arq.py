"""ARQ enqueue pool for the API process (Phase 12a).

The API needs to enqueue background jobs (e.g. an admin-triggered historical
re-scan). Workers get their pool from the ARQ context; the API opens its own
short-lived pool per request via this dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings


async def get_arq_pool() -> AsyncIterator[ArqRedis]:
    """FastAPI dependency yielding an ARQ pool, closed after the request."""
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        yield pool
    finally:
        await pool.aclose()
