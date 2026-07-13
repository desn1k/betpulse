"""Async Redis client factory.

Redis backs rate-limit buckets and per-account lockout counters (and, in later
phases, cache and live pub/sub). A single lazily-created client is shared across
the process.
"""

from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(
        settings.redis_url,
        password=settings.redis_password or None,
        encoding="utf-8",
        decode_responses=True,
    )


def reset_redis() -> None:
    """Clear the cached client (used by tests after env changes)."""
    get_redis.cache_clear()
