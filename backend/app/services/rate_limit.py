"""Redis-backed rate limiting.

This module implements the *per-IP* half of the login defence (a fixed-window
counter). The *per-account* half — exponential-backoff lockout — lives on the
user row and is handled in :mod:`app.services.auth`, because permanently locking
an account on repeated wrong passwords would let anyone lock out any user.
"""

from __future__ import annotations

from redis.asyncio import Redis


class RateLimitExceeded(Exception):
    """Raised when a fixed-window rate limit is exceeded."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("rate limit exceeded")


async def enforce_fixed_window(redis: Redis, *, key: str, limit: int, window_seconds: int) -> None:
    """Increment a fixed-window counter and raise if it exceeds ``limit``."""
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    if count > limit:
        ttl = await redis.ttl(key)
        raise RateLimitExceeded(retry_after=ttl if ttl and ttl > 0 else window_seconds)


async def enforce_login_ip_limit(redis: Redis, *, ip: str, limit: int) -> None:
    """Per-IP login attempt limit (per minute)."""
    await enforce_fixed_window(redis, key=f"rl:login:ip:{ip}", limit=limit, window_seconds=60)
