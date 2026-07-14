"""Rolling out-of-sample evaluation for the champion re-evaluation job."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml import metrics as metrics_mod
from app.ml.registry import MethodMetrics
from app.models.fixture import Fixture
from app.models.market import Odds
from app.models.prediction import Prediction

_OUTCOME_INDEX = {"home": 0, "draw": 1, "away": 2}


def _label(fx: Fixture) -> int:
    if fx.ft_home > fx.ft_away:  # type: ignore[operator]
        return 0
    if fx.ft_home == fx.ft_away:
        return 1
    return 2


async def compute_rolling_metrics(
    session: AsyncSession, *, window_days: int, now: datetime | None = None
) -> dict[str, MethodMetrics]:
    now = now or datetime.now(UTC)
    start = now - timedelta(days=window_days)

    fixtures = (
        (
            await session.execute(
                select(Fixture).where(
                    Fixture.ft_home.is_not(None),
                    Fixture.kickoff_at >= start,
                    Fixture.kickoff_at <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    if not fixtures:
        return {}

    fixture_ids = [fx.id for fx in fixtures]
    labels = {fx.id: _label(fx) for fx in fixtures}
    odds = await _closing_odds(session, fixture_ids)

    preds = (
        (
            await session.execute(
                select(Prediction).where(
                    Prediction.fixture_id.in_(fixture_ids), Prediction.market == "1x2"
                )
            )
        )
        .scalars()
        .all()
    )

    grouped: dict[str, dict[uuid.UUID, dict[str, float]]] = {}
    for p in preds:
        grouped.setdefault(p.method, {}).setdefault(p.fixture_id, {})[p.outcome] = float(
            p.probability
        )

    result: dict[str, MethodMetrics] = {}
    for method, by_fixture in grouped.items():
        probs_list, y_list, odds_list = [], [], []
        for fid, outcomes in by_fixture.items():
            if {"home", "draw", "away"} <= set(outcomes):
                probs_list.append([outcomes["home"], outcomes["draw"], outcomes["away"]])
                y_list.append(labels[fid])
                fo = odds.get(fid, {})
                odds_list.append([fo.get("home", 0.0), fo.get("draw", 0.0), fo.get("away", 0.0)])
        if not probs_list:
            continue
        probs = np.array(probs_list)
        y = np.array(y_list)
        brier = metrics_mod.brier_multiclass(probs, y)
        baseline = metrics_mod.brier_baseline(y)
        result[method] = MethodMetrics(
            accuracy_pct=metrics_mod.accuracy_pct(brier, baseline),
            brier=brier,
            log_loss=metrics_mod.log_loss(probs, y),
            roi_vs_closing=metrics_mod.roi_vs_closing(probs, y, np.array(odds_list)),
            sample_count=len(y),
        )
    return result


async def _closing_odds(
    session: AsyncSession, fixture_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, float]]:
    rows = (
        (
            await session.execute(
                select(Odds).where(
                    Odds.fixture_id.in_(fixture_ids),
                    Odds.market == "1x2",
                    Odds.bookmaker == "pinnacle",
                )
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, dict[str, float]] = {}
    for o in rows:
        out.setdefault(o.fixture_id, {})[o.outcome] = float(o.price)
    return out
