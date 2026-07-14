"""Nightly champion re-evaluation over the DB, incl. strict idempotency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.core.redis import get_redis
from app.ml.registry import CHAMPION_PROMOTED
from app.models.audit_log import AuditLog
from app.models.fixture import Fixture, FixtureStatus
from app.models.market import Odds
from app.models.model_registry import ModelRegistry, ModelStatus
from app.models.prediction import Prediction
from app.models.reference import League, Team
from app.workers.tasks import CHAMPION_LOCK_KEY, reevaluate_champions, reevaluate_champions_task
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_NOW = datetime(2026, 7, 14, tzinfo=UTC)
_OUTCOMES = ("home", "draw", "away")
# Six recent fixtures with mixed outcomes.
_RESULTS = [(2, 0, 0), (0, 1, 2), (1, 1, 1), (3, 1, 0), (0, 2, 2), (1, 1, 1)]


async def _seed(session: AsyncSession) -> None:
    league = League(code="EPL", name="Premier League", country="England")
    session.add(league)
    teams = [Team(name=f"T{i}", normalized_name=f"t{i}") for i in range(12)]
    session.add_all(teams)
    await session.flush()

    for reg in ("elo", "dixon_coles"):
        session.add(
            ModelRegistry(
                method=reg,
                version="v1",
                status=ModelStatus.challenger,
                is_enabled=True,
                is_visible=True,
                display_weight=Decimal("0"),
                min_samples=1,
                sample_count=0,
            )
        )

    for idx, (hg, ag, label) in enumerate(_RESULTS):
        fx = Fixture(
            league_id=league.id,
            season="2025-2026",
            home_team_id=teams[idx * 2].id,
            away_team_id=teams[idx * 2 + 1].id,
            kickoff_at=_NOW - timedelta(days=10 + idx),
            status=FixtureStatus.finished,
            ft_home=hg,
            ft_away=ag,
        )
        session.add(fx)
        await session.flush()
        for k, outcome in enumerate(_OUTCOMES):
            session.add(
                Odds(
                    fixture_id=fx.id,
                    bookmaker="pinnacle",
                    market="1x2",
                    outcome=outcome,
                    ts=fx.kickoff_at,
                    price=Decimal("2.5"),
                )
            )
            # dixon_coles nails the actual outcome; elo is uninformative.
            session.add(
                Prediction(
                    fixture_id=fx.id,
                    method="dixon_coles",
                    market="1x2",
                    outcome=outcome,
                    probability=Decimal("0.70") if k == label else Decimal("0.15"),
                    model_version="v1",
                )
            )
            session.add(
                Prediction(
                    fixture_id=fx.id,
                    method="elo",
                    market="1x2",
                    outcome=outcome,
                    probability=Decimal("0.34") if k == 0 else Decimal("0.33"),
                    model_version="v1",
                )
            )
    await session.flush()


@pytest.mark.asyncio
async def test_reevaluate_promotes_best_and_is_idempotent(session: AsyncSession) -> None:
    await _seed(session)

    first = await reevaluate_champions(
        session, window_days=90, min_samples=1, weight_mode="auto", now=_NOW
    )
    second = await reevaluate_champions(
        session, window_days=90, min_samples=1, weight_mode="auto", now=_NOW
    )
    assert first == "dixon_coles"
    assert second == "dixon_coles"

    champions = await session.scalar(
        select(func.count())
        .select_from(ModelRegistry)
        .where(ModelRegistry.status == ModelStatus.champion)
    )
    promotions = await session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == CHAMPION_PROMOTED)
    )
    assert champions == 1
    assert promotions == 1

    dc = (
        await session.execute(select(ModelRegistry).where(ModelRegistry.method == "dixon_coles"))
    ).scalar_one()
    assert dc.accuracy_pct is not None
    assert dc.last_evaluated_at is not None


@pytest.mark.asyncio
async def test_task_acquires_lock_then_releases_it() -> None:
    redis = get_redis()
    result = await reevaluate_champions_task({})
    assert result is None  # empty DB → no champion
    # The lock is released (and would in any case expire via its TTL).
    assert await redis.get(CHAMPION_LOCK_KEY) is None


@pytest.mark.asyncio
async def test_task_skips_when_lock_is_held() -> None:
    redis = get_redis()
    await redis.set(CHAMPION_LOCK_KEY, "someone-else", ex=60)
    assert await reevaluate_champions_task({}) is None
    # The other holder's lock is untouched.
    assert await redis.get(CHAMPION_LOCK_KEY) == "someone-else"
