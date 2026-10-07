"""Per-day usage limits (spec §7 tier enforcement).

The match-detail view limit is a **calendar-day** budget that resets exactly at
UTC midnight, independent of when the first request of the day happened. We
therefore key the Redis counter by the UTC date and set the TTL to the number of
seconds remaining until the next UTC midnight — not a rolling 24h window.

    key = limits:{identity}:{YYYY-MM-DD}     TTL = seconds to next UTC midnight

``identity`` is the user id for an authenticated caller, or the client IP for a
guest. A limit of ``-1`` means unlimited (pro/expert) and is never counted.

The budget is for **distinct** matches (F6): the match page refetches its card
every 60 s, and opening a match again the same day must cost nothing. The
fixtures already charged today sit in a set next to the counter, with the same
date and TTL::

    key = limits:seen:{identity}:{YYYY-MM-DD}   (set of fixture ids)

The counter stays the measure of what was used (``matches_remaining``); the set
only decides whether a view is new. A set lost on its own (evicted) makes the
next view of a match count again, never an error.

Every counter update is one atomic Redis script (:mod:`app.services.counters`):
budgets are taken with check-and-increment, so concurrent callers can never
exceed a limit, and a key always gets its TTL in the same step.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from app.services.counters import (
    decr_floor_zero,
    incr_distinct_within_limit,
    incr_with_ttl,
    incr_within_limit,
)

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
    count = await incr_with_ttl(redis, key, seconds_until_next_hour(now))
    if count > limit:
        raise RateLimited(seconds_until_next_hour(now))


def seconds_until_utc_midnight(now: datetime) -> int:
    """Whole seconds from ``now`` until the next 00:00:00 UTC (>= 1)."""
    now = now.astimezone(UTC)
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((next_midnight - now).total_seconds()))


def _key(identity: str, now: datetime) -> str:
    return f"limits:{identity}:{now.astimezone(UTC):%Y-%m-%d}"


def _seen_key(identity: str, now: datetime) -> str:
    return f"limits:seen:{identity}:{now.astimezone(UTC):%Y-%m-%d}"


async def consume_match_view(
    redis: Redis,
    *,
    identity: str,
    fixture_id: uuid.UUID,
    limit: int,
    now: datetime | None = None,
) -> int:
    """Count a view of ``fixture_id`` against the day's budget of distinct matches.

    A match already viewed today costs nothing. Returns the number of views
    remaining (``UNLIMITED`` for an unlimited tier). Raises :class:`LimitExceeded`
    — without counting or recording the match — when the budget is already spent
    and the match is new today.
    """
    if limit == UNLIMITED:
        return UNLIMITED
    now = now or datetime.now(UTC)
    count = await incr_distinct_within_limit(
        redis,
        _key(identity, now),
        _seen_key(identity, now),
        str(fixture_id),
        limit=limit,
        ttl_seconds=seconds_until_utc_midnight(now),
    )
    if count is None:
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
    count = await incr_within_limit(
        redis, key, limit=limit, ttl_seconds=seconds_until_utc_midnight(now)
    )
    if count is None:
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


async def record_push_delivered(
    redis: Redis, *, user_id: uuid.UUID, now: datetime | None = None
) -> None:
    """Count one push against the user's UTC-day budget (TTL to the next UTC
    midnight) unconditionally — no limit check."""
    now = now or datetime.now(UTC)
    await incr_with_ttl(redis, _push_key(user_id, now), seconds_until_utc_midnight(now))


async def reserve_push(
    redis: Redis, *, user_id: uuid.UUID, limit: int, now: datetime | None = None
) -> bool:
    """Take one push from the user's UTC-day budget **before** delivering.

    Atomic check-and-increment: concurrent dispatches (parallel realtime jobs
    for different fixtures) can never reserve more than ``limit`` in total.
    Returns ``False`` when the budget is spent. An unlimited tier (``-1``) is
    still counted. Pair every reservation that is not delivered with
    :func:`release_push`; a crash in between loses one unit — fewer pushes,
    never more."""
    now = now or datetime.now(UTC)
    if limit == UNLIMITED:
        await record_push_delivered(redis, user_id=user_id, now=now)
        return True
    count = await incr_within_limit(
        redis, _push_key(user_id, now), limit=limit, ttl_seconds=seconds_until_utc_midnight(now)
    )
    return count is not None


async def release_push(redis: Redis, *, user_id: uuid.UUID, now: datetime | None = None) -> None:
    """Return a reservation whose push was not delivered (never below zero)."""
    now = now or datetime.now(UTC)
    await decr_floor_zero(redis, _push_key(user_id, now))
