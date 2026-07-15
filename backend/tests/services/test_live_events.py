"""SSE plumbing: Redis pub/sub fan-out across replicas and reconnect replay."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import get_settings
from app.core.redis import get_redis
from app.models.fixture import Fixture, FixtureStatus
from app.models.live import LiveUpdate
from app.models.reference import League, Team
from app.services.live.events import (
    format_sse,
    publish_live_update,
    replay_since,
    subscribe_live_updates,
)
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

CHANNEL = "test:live:events"


@pytest.mark.asyncio
async def test_publish_on_one_replica_is_received_on_another() -> None:
    publisher = get_redis()  # replica A
    subscriber = Redis.from_url(get_settings().redis_url, decode_responses=True)  # replica B
    try:
        agen = subscribe_live_updates(subscriber, CHANNEL)
        first = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.2)  # let the SUBSCRIBE take effect

        await publish_live_update(publisher, CHANNEL, 42, {"fixture_id": "abc", "minute": 55})
        event = await asyncio.wait_for(first, timeout=3.0)

        assert event["id"] == 42
        assert event["payload"]["minute"] == 55
        await agen.aclose()
    finally:
        await subscriber.aclose()


async def _make_fixture(session: AsyncSession) -> uuid.UUID:
    league = League(code="EPL", name="Premier League")
    home = Team(name="Arsenal", normalized_name="arsenal")
    away = Team(name="Chelsea", normalized_name="chelsea")
    session.add_all([league, home, away])
    await session.flush()
    fixture = Fixture(
        league_id=league.id,
        season="2025",
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=datetime.now(tz=UTC),
        status=FixtureStatus.live,
    )
    session.add(fixture)
    await session.flush()
    return fixture.id


@pytest.mark.asyncio
async def test_replay_returns_newer_ids_within_window(session: AsyncSession) -> None:
    fid = await _make_fixture(session)
    now = datetime.now(tz=UTC)

    def _payload(minute: int) -> dict[str, object]:
        return {"fixture_id": str(fid), "minute": minute, "probs": {}}

    stale = LiveUpdate(
        fixture_id=fid,
        minute=1,
        home_score=0,
        away_score=0,
        payload=_payload(1),
        created_at=now - timedelta(hours=1),
    )
    recent_a = LiveUpdate(
        fixture_id=fid, minute=50, home_score=1, away_score=0, payload=_payload(50)
    )
    recent_b = LiveUpdate(
        fixture_id=fid, minute=60, home_score=1, away_score=1, payload=_payload(60)
    )
    session.add_all([stale, recent_a, recent_b])
    await session.flush()

    # Replay everything after the stale row, bounded to a 5-minute window: the
    # hour-old row is excluded even though its id is greater than last_event_id.
    result = await replay_since(session, last_event_id=stale.id, window_seconds=300, now=now)
    ids = [u.id for u in result]
    assert ids == [recent_a.id, recent_b.id]


def test_format_sse_frames_id_and_data() -> None:
    frame = format_sse(7, {"minute": 12})
    assert frame == 'id: 7\ndata: {"minute": 12}\n\n'
