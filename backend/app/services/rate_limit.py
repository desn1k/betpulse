"""Redis-backed rate limiting.

This module implements the *per-IP* half of the login defence (a fixed-window
counter). The *per-account* half — exponential-backoff lockout — lives on the
user row and is handled in :mod:`app.services.auth`, because permanently locking
an account on repeated wrong passwords would let anyone lock out any user.
"""

from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.client_ip import rate_limit_bucket
from app.services.counters import incr_with_ttl

logger = logging.getLogger(__name__)

# The refresh limit's Redis call may delay a refresh this long at most (F7).
REFRESH_LIMIT_TIMEOUT_SECONDS = 0.5


class RateLimitExceeded(Exception):
    """Raised when a fixed-window rate limit is exceeded."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("rate limit exceeded")


async def enforce_fixed_window(redis: Redis, *, key: str, limit: int, window_seconds: int) -> None:
    """Increment a fixed-window counter and raise if it exceeds ``limit``.

    INCR and EXPIRE are one atomic step: the key is not time-bucketed, so a
    counter left without a TTL would lock the caller out for good."""
    count = await incr_with_ttl(redis, key, window_seconds)
    if count > limit:
        ttl = await redis.ttl(key)
        raise RateLimitExceeded(retry_after=ttl if ttl and ttl > 0 else window_seconds)


async def enforce_login_ip_limit(redis: Redis, *, ip: str, limit: int) -> None:
    """Per-IP login attempt limit (per minute; IPv6 per /64)."""
    await enforce_fixed_window(
        redis, key=f"rl:login:ip:{rate_limit_bucket(ip)}", limit=limit, window_seconds=60
    )


async def enforce_llm_analysis_limit(redis: Redis, *, identity: str, limit: int) -> None:
    """Per-caller LLM analysis request limit (per minute)."""
    await enforce_fixed_window(
        redis, key=f"rl:llm_analysis:{identity}", limit=limit, window_seconds=60
    )


async def enforce_match_detail_limit(
    redis: Redis, *, identity: str, limit: int, window_seconds: int
) -> None:
    """Per-caller match-detail request limit, before the fixture lookup."""
    await enforce_fixed_window(
        redis, key=f"rl:match_detail:{identity}", limit=limit, window_seconds=window_seconds
    )


async def enforce_match_list_limit(
    redis: Redis, *, identity: str, limit: int, window_seconds: int
) -> None:
    """Per-caller limit on the public match list (F7)."""
    await enforce_fixed_window(
        redis, key=f"rl:match_list:{identity}", limit=limit, window_seconds=window_seconds
    )


async def enforce_refresh_ip_limit(redis: Redis, *, ip: str, limit: int) -> None:
    """Per-IP limit on POST /auth/refresh (per minute; IPv6 per /64), before the
    rotation (F7).

    **Fails open**, unlike the other limits: when Redis cannot be reached (or
    takes longer than ``REFRESH_LIMIT_TIMEOUT_SECONDS``) the refresh goes ahead
    unlimited and a warning is logged. The rotation itself needs only the
    database (the F13 replay is fail-open too), so failing closed would stop
    every signed-in user from renewing a session during a Redis outage for the
    sake of a limit; the routes that fail closed need Redis for their own work
    anyway (quotas). Raises ``RateLimitExceeded`` only for a real excess."""
    try:
        await asyncio.wait_for(
            enforce_fixed_window(
                redis,
                key=f"rl:refresh:ip:{rate_limit_bucket(ip)}",
                limit=limit,
                window_seconds=60,
            ),
            timeout=REFRESH_LIMIT_TIMEOUT_SECONDS,
        )
    except (RedisError, OSError, TimeoutError) as exc:
        logger.warning("refresh rate limit not applied (fail-open): %s", type(exc).__name__)


async def enforce_user_limit(
    redis: Redis, *, scope: str, user_id: str, limit: int, window_seconds: int
) -> None:
    """Per-user fixed window for an account-security action (password change,
    TOTP setup or code check). Every attempt counts, successes included."""
    await enforce_fixed_window(
        redis, key=f"rl:{scope}:user:{user_id}", limit=limit, window_seconds=window_seconds
    )


async def enforce_admin_mutation_ip_limit(redis: Redis, *, ip: str, limit: int) -> None:
    """Per-IP admin mutation limit (per minute; IPv6 per /64)."""
    await enforce_fixed_window(
        redis, key=f"rl:admin_mutation:ip:{rate_limit_bucket(ip)}", limit=limit, window_seconds=60
    )
