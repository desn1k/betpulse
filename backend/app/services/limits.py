"""Per-day usage limits (spec §7 tier enforcement).

The match-detail view limit is a **calendar-day** budget that resets exactly at
UTC midnight, independent of when the first request of the day happened. We
therefore key the Redis counter by the UTC date and set the TTL to the number of
seconds remaining until the next UTC midnight — not a rolling 24h window.

    key = limits:{identity}:{YYYY-MM-DD}     TTL = seconds to next UTC midnight

``identity`` is the user id for an authenticated caller, or the client IP for a
guest. A limit of ``-1`` means unlimited (pro/expert) and is never counted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

UNLIMITED = -1


class LimitExceeded(Exception):
    """Raised when a per-day usage limit is exhausted."""


def seconds_until_utc_midnight(now: datetime) -> int:
    """Whole seconds from ``now`` until the next 00:00:00 UTC (>= 1)."""
    now = now.astimezone(UTC)
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((next_midnight - now).total_seconds()))


def _key(identity: str, now: datetime) -> str:
    return f"limits:{identity}:{now.astimezone(UTC):%Y-%m-%d}"


async def consume_match_view(
    redis: Redis, *, identity: str, limit: int, now: datetime | None = None
) -> int:
    """Count one match-detail view against the day's budget.

    Returns the number of views remaining **after** this one (``UNLIMITED`` for an
    unlimited tier). Raises :class:`LimitExceeded` — without consuming — when the
    budget is already spent.
    """
    if limit == UNLIMITED:
        return UNLIMITED
    now = now or datetime.now(UTC)
    key = _key(identity, now)
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, seconds_until_utc_midnight(now))
    if count > limit:
        # Roll back the over-limit increment so the stored count reflects reality.
        await redis.decr(key)
        raise LimitExceeded
    return limit - count


async def match_views_remaining(
    redis: Redis, *, identity: str, limit: int, now: datetime | None = None
) -> int | None:
    """Views left today without consuming any. ``None`` = unlimited."""
    if limit == UNLIMITED:
        return None
    now = now or datetime.now(UTC)
    used = await redis.get(_key(identity, now))
    used_count = int(used) if used is not None else 0
    return max(0, limit - used_count)
