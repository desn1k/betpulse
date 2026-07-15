"""Daily LLM ranking service (spec §8, owner decision).

Ranks today's scheduled fixtures by ``agreement% × |consensus − market|``. The
top-scoring fixture becomes ``fixture_llm_rank == 1`` (match of the day).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.models.fixture import Fixture, FixtureStatus
from app.models.model_registry import ModelRegistry, ModelStatus
from app.models.prediction import Prediction
from app.models.reference import League, Team
from app.services.llm.ranking import rank_today_fixtures
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_VISIBLE = ("elo", "dixon_coles", "xg", "lightgbm")


async def _rank(session: AsyncSession, fixture_id: uuid.UUID) -> int | None:
    fx = await session.get(Fixture, fixture_id)
    assert fx is not None
    return fx.fixture_llm_rank


async def _seed_registry(session: AsyncSession) -> None:
    session.add_all(
        [
            ModelRegistry(
                method=method,
                version="v1",
                status=ModelStatus.challenger,
                is_enabled=True,
                is_visible=True,
                display_weight=Decimal("10"),
                accuracy_pct=Decimal("50.0"),
                sample_count=400,
            )
            for method in _VISIBLE
        ]
    )
    await session.flush()


async def _seed_fixture(
    session: AsyncSession,
    *,
    kickoff: datetime,
    method_home: dict[str, float],
    status: FixtureStatus = FixtureStatus.scheduled,
) -> uuid.UUID:
    league = League(code=f"L{uuid.uuid4().hex[:4]}", name="League")
    home = Team(name="H", normalized_name=f"h-{uuid.uuid4().hex[:6]}")
    away = Team(name="A", normalized_name=f"a-{uuid.uuid4().hex[:6]}")
    session.add_all([league, home, away])
    await session.flush()
    fixture = Fixture(
        league_id=league.id,
        season="2025-2026",
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=kickoff,
        status=status,
    )
    session.add(fixture)
    await session.flush()
    for method, home_p in method_home.items():
        for outcome, prob in (("home", home_p), ("draw", 0.2), ("away", round(0.8 - home_p, 3))):
            session.add(
                Prediction(
                    fixture_id=fixture.id,
                    method=method,
                    market="1x2",
                    outcome=outcome,
                    probability=Decimal(str(prob)),
                    model_version="v1",
                )
            )
    await session.flush()
    return fixture.id


@pytest.mark.asyncio
async def test_ranks_by_confidence_times_edge(session: AsyncSession) -> None:
    await _seed_registry(session)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    day = now.replace(hour=8)

    # High score: methods tightly agree (0.60) AND far from market (0.40) → big edge.
    strong = await _seed_fixture(
        session,
        kickoff=day,
        method_home={
            "elo": 0.60,
            "dixon_coles": 0.60,
            "xg": 0.60,
            "lightgbm": 0.60,
            "consensus": 0.60,
            "market": 0.40,
        },
    )
    # Low score: methods split AND consensus hugs the market → tiny edge.
    weak = await _seed_fixture(
        session,
        kickoff=day,
        method_home={
            "elo": 0.40,
            "dixon_coles": 0.60,
            "xg": 0.30,
            "lightgbm": 0.70,
            "consensus": 0.50,
            "market": 0.49,
        },
    )
    await session.commit()

    ranked = await rank_today_fixtures(session, now=now)
    await session.commit()

    assert ranked == 2
    assert await _rank(session, strong) == 1
    assert await _rank(session, weak) == 2


@pytest.mark.asyncio
async def test_only_today_scheduled_are_ranked(session: AsyncSession) -> None:
    await _seed_registry(session)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    probs = {
        "elo": 0.60,
        "dixon_coles": 0.60,
        "xg": 0.60,
        "lightgbm": 0.60,
        "consensus": 0.60,
        "market": 0.40,
    }

    today = await _seed_fixture(session, kickoff=now.replace(hour=20), method_home=probs)
    tomorrow = await _seed_fixture(
        session, kickoff=now + timedelta(days=1), method_home=probs
    )
    finished = await _seed_fixture(
        session, kickoff=now.replace(hour=9), method_home=probs, status=FixtureStatus.finished
    )
    await session.commit()

    ranked = await rank_today_fixtures(session, now=now)
    await session.commit()

    assert ranked == 1
    assert await _rank(session, today) == 1
    assert await _rank(session, tomorrow) is None
    assert await _rank(session, finished) is None


@pytest.mark.asyncio
async def test_no_consensus_is_not_ranked(session: AsyncSession) -> None:
    await _seed_registry(session)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    # No consensus method among predictions → excluded from scoring.
    fx = await _seed_fixture(
        session,
        kickoff=now.replace(hour=18),
        method_home={"elo": 0.55, "xg": 0.6, "market": 0.4},
    )
    await session.commit()

    ranked = await rank_today_fixtures(session, now=now)
    await session.commit()

    assert ranked == 0
    assert await _rank(session, fx) is None


@pytest.mark.asyncio
async def test_rerank_clears_stale_ranks(session: AsyncSession) -> None:
    await _seed_registry(session)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    probs = {
        "elo": 0.60,
        "dixon_coles": 0.60,
        "xg": 0.60,
        "lightgbm": 0.60,
        "consensus": 0.60,
        "market": 0.40,
    }
    # A fixture ranked yesterday whose kickoff is now in the past must lose its rank.
    stale = await _seed_fixture(
        session, kickoff=now - timedelta(days=1), method_home=probs
    )
    stale_fx = await session.get(Fixture, stale)
    assert stale_fx is not None
    stale_fx.fixture_llm_rank = 1
    await session.commit()

    await rank_today_fixtures(session, now=now)
    await session.commit()

    assert await _rank(session, stale) is None


@pytest.mark.asyncio
async def test_ranks_are_written_to_db(session: AsyncSession) -> None:
    await _seed_registry(session)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    probs = {
        "elo": 0.60,
        "dixon_coles": 0.60,
        "xg": 0.60,
        "lightgbm": 0.60,
        "consensus": 0.60,
        "market": 0.40,
    }
    await _seed_fixture(session, kickoff=now.replace(hour=20), method_home=probs)
    await session.commit()
    await rank_today_fixtures(session, now=now)
    await session.commit()

    ranked_rows = (
        (
            await session.execute(
                select(Fixture.fixture_llm_rank).where(Fixture.fixture_llm_rank.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    assert list(ranked_rows) == [1]
