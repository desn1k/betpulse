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

import uuid
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

UNLIMITED = -1


class LimitExceeded(Exception):
    """Raised when a per-day usage limit is exhausted."""


class RateLimited(Exception):
    """Raised when a per-hour action rate limit is exceeded."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("rate limited")


def seconds_until_next_hour(now: datetime) -> int:
    now = now.astimezone(UTC)
    nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return max(1, int((nxt - now).total_seconds()))


async def enforce_promo_redeem_limit(
    redis: Redis, *, user_id: uuid.UUID, limit: int, now: datetime | None = None
) -> None:
    """Per-user, per-hour promo-redemption limit. Key is bucketed by the clock
    hour (``rate_limit:promo:{user_id}:{YYYY-MM-DD-HH}``) so it resets on the hour.
    Raises :class:`RateLimited` with ``retry_after`` seconds when exceeded."""
    now = now or datetime.now(UTC)
    key = f"rate_limit:promo:{user_id}:{now.astimezone(UTC):%Y-%m-%d-%H}"
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, seconds_until_next_hour(now))
    if count > limit:
        raise RateLimited(seconds_until_next_hour(now))


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


async def consume_backtester_run(
    redis: Redis, *, user_id: uuid.UUID, limit: int, now: datetime | None = None
) -> None:
    """Count one backtester run against the day's budget (spec §7). Key
    ``limits:backtester:{user_id}:{YYYY-MM-DD}``, reset at UTC midnight. Raises
    :class:`LimitExceeded` when the budget is spent (``-1`` = unlimited)."""
    if limit == UNLIMITED:
        return
    now = now or datetime.now(UTC)
    key = f"limits:backtester:{user_id}:{now.astimezone(UTC):%Y-%m-%d}"
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, seconds_until_utc_midnight(now))
    if count > limit:
        await redis.decr(key)
        raise LimitExceeded


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


def _push_key(user_id: uuid.UUID, now: datetime) -> str:
    return f"limits:push:{user_id}:{now.astimezone(UTC):%Y-%m-%d}"


async def push_budget_remaining(
    redis: Redis, *, user_id: uuid.UUID, limit: int, now: datetime | None = None
) -> int | None:
    """Delivered pushes left today for a user (spec §7, Phase 11), without
    consuming any. ``None`` = unlimited; ``0`` = exhausted (or a no-push tier).
    The budget is a hard-stop checked **before** delivery so we never overspend."""
    if limit == UNLIMITED:
        return None
    now = now or datetime.now(UTC)
    used = await redis.get(_push_key(user_id, now))
    used_count = int(used) if used is not None else 0
    return max(0, limit - used_count)


async def record_push_delivered(
    redis: Redis, *, user_id: uuid.UUID, now: datetime | None = None
) -> None:
    """Count one **delivered** push against the user's UTC-day budget (TTL to the
    next UTC midnight). Called only after a successful delivery, so failed pushes
    never consume the budget."""
    now = now or datetime.now(UTC)
    key = _push_key(user_id, now)
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, seconds_until_utc_midnight(now))
