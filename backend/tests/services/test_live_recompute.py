"""In-play recompute: state-change gating and swing detection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.models.fixture import Fixture, FixtureStatus
from app.models.live import LiveUpdate
from app.models.prediction import PredictionLive
from app.models.reference import League, Team
from app.services.live.recompute import BaseRates, recompute_fixture
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

RATES = BaseRates(lam_home=1.4, lam_away=1.4, rho=-0.05)


async def _make_fixture(session: AsyncSession) -> uuid.UUID:
    league = League(code="EPL", name="Premier League", country="England")
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
        minute=1,
    )
    session.add(fixture)
    await session.flush()
    return fixture.id


@pytest.mark.asyncio
async def test_first_recompute_writes_rows_but_does_not_push(session: AsyncSession) -> None:
    fid = await _make_fixture(session)
    result = await recompute_fixture(
        session,
        fixture_id=fid,
        minute=10,
        home_score=0,
        away_score=0,
        base_rates=RATES,
        swing_threshold=0.10,
    )
    assert result.changed is True
    assert result.should_push is False  # no previous row to swing from
    assert result.live_update_id is not None

    preds = (await session.execute(select(func.count()).select_from(PredictionLive))).scalar_one()
    assert preds == 3  # home / draw / away


@pytest.mark.asyncio
async def test_unchanged_state_is_skipped(session: AsyncSession) -> None:
    fid = await _make_fixture(session)
    now = datetime.now(tz=UTC)
    await recompute_fixture(
        session,
        fixture_id=fid,
        minute=10,
        home_score=0,
        away_score=0,
        base_rates=RATES,
        swing_threshold=0.10,
        now=now,
    )
    second = await recompute_fixture(
        session,
        fixture_id=fid,
        minute=10,
        home_score=0,
        away_score=0,
        base_rates=RATES,
        swing_threshold=0.10,
        now=now + timedelta(seconds=30),
    )
    assert second.changed is False
    events = (await session.execute(select(func.count()).select_from(LiveUpdate))).scalar_one()
    assert events == 1  # no second event appended


@pytest.mark.asyncio
async def test_large_swing_flags_push(session: AsyncSession) -> None:
    fid = await _make_fixture(session)
    now = datetime.now(tz=UTC)
    await recompute_fixture(
        session,
        fixture_id=fid,
        minute=10,
        home_score=0,
        away_score=0,
        base_rates=RATES,
        swing_threshold=0.10,
        now=now,
    )
    swung = await recompute_fixture(
        session,
        fixture_id=fid,
        minute=85,
        home_score=2,
        away_score=0,
        base_rates=RATES,
        swing_threshold=0.10,
        now=now + timedelta(minutes=75),
    )
    assert swung.changed is True
    assert swung.swing > 0.10
    assert swung.should_push is True


@pytest.mark.asyncio
async def test_small_change_does_not_push(session: AsyncSession) -> None:
    fid = await _make_fixture(session)
    now = datetime.now(tz=UTC)
    await recompute_fixture(
        session,
        fixture_id=fid,
        minute=84,
        home_score=2,
        away_score=0,
        base_rates=RATES,
        swing_threshold=0.10,
        now=now,
    )
    # One minute later, same score: probabilities barely move.
    tiny = await recompute_fixture(
        session,
        fixture_id=fid,
        minute=85,
        home_score=2,
        away_score=0,
        base_rates=RATES,
        swing_threshold=0.10,
        now=now + timedelta(minutes=1),
    )
    assert tiny.changed is True
    assert tiny.swing < 0.10
    assert tiny.should_push is False
