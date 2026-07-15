"""Per-day usage-limit counters (spec §7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.redis import get_redis
from app.services.limits import (
    UNLIMITED,
    LimitExceeded,
    consume_match_view,
    match_views_remaining,
    seconds_until_utc_midnight,
)


def test_seconds_until_utc_midnight() -> None:
    # 23:00:00 UTC → one hour to midnight.
    assert seconds_until_utc_midnight(datetime(2026, 7, 15, 23, 0, 0, tzinfo=UTC)) == 3600
    # A value just after midnight is nearly a full day.
    almost = seconds_until_utc_midnight(datetime(2026, 7, 15, 0, 0, 1, tzinfo=UTC))
    assert 86_390 < almost <= 86_399


@pytest.mark.asyncio
async def test_consume_counts_down_and_blocks() -> None:
    redis = get_redis()
    # limit 3: three views succeed (remaining 2,1,0), the fourth is refused.
    assert await consume_match_view(redis, identity="ip1", limit=3) == 2
    assert await consume_match_view(redis, identity="ip1", limit=3) == 1
    assert await consume_match_view(redis, identity="ip1", limit=3) == 0
    with pytest.raises(LimitExceeded):
        await consume_match_view(redis, identity="ip1", limit=3)
    # A different identity has its own budget.
    assert await consume_match_view(redis, identity="ip2", limit=3) == 2


@pytest.mark.asyncio
async def test_unlimited_never_counts() -> None:
    redis = get_redis()
    assert await consume_match_view(redis, identity="u", limit=UNLIMITED) == UNLIMITED
    assert await match_views_remaining(redis, identity="u", limit=UNLIMITED) is None
    # No key was written for an unlimited tier.
    now = datetime.now(UTC)
    assert await redis.get(f"limits:u:{now:%Y-%m-%d}") is None


@pytest.mark.asyncio
async def test_remaining_reflects_consumption() -> None:
    redis = get_redis()
    assert await match_views_remaining(redis, identity="ip3", limit=10) == 10
    await consume_match_view(redis, identity="ip3", limit=10)
    await consume_match_view(redis, identity="ip3", limit=10)
    assert await match_views_remaining(redis, identity="ip3", limit=10) == 8


@pytest.mark.asyncio
async def test_key_is_utc_date_scoped_with_midnight_ttl() -> None:
    redis = get_redis()
    now = datetime(2026, 7, 15, 22, 0, 0, tzinfo=UTC)
    await consume_match_view(redis, identity="ipd", limit=5, now=now)
    key = "limits:ipd:2026-07-15"
    assert await redis.get(key) == "1"
    # TTL is time-to-next-UTC-midnight (2h here), not a rolling 24h window.
    ttl = await redis.ttl(key)
    assert 7100 < ttl <= 7200
