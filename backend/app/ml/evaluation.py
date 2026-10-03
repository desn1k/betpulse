"""Rolling evaluation for the champion re-evaluation job.

What is scored, explicitly:

* **one model version per method** — the caller's ``versions`` or, by default,
  each method's latest registered version (``model_registry.last_trained_at``,
  then version). Predictions of other versions never mix in, not even per
  outcome;
* **the same fixtures for every method compared** — the fixtures every
  participating method predicted completely in the window. A method with fewer
  than ``min_samples`` complete predictions does not participate (and so gets
  no metrics), rather than shrinking the common set for everyone;
* ROI settles at the **closing quote** chosen by :mod:`app.ml.odds_selection`.

Protocol: the predictions being scored were produced by a chronological,
batch-wise pass (each one from matches strictly before its kickoff), so these
are *prequential* metrics on historical matches — not a verified forward test.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml import metrics as metrics_mod
from app.ml.odds_selection import closing_quotes
from app.ml.registry import MethodMetrics
from app.models.fixture import Fixture
from app.models.model_registry import ModelRegistry
from app.models.prediction import Prediction

EVALUATION_PROTOCOL = "prequential_historical"
_OUTCOMES = ("home", "draw", "away")


def _label(fx: Fixture) -> int:
    if fx.ft_home > fx.ft_away:  # type: ignore[operator]
        return 0
    if fx.ft_home == fx.ft_away:
        return 1
    return 2


async def latest_versions(session: AsyncSession) -> dict[str, str]:
    """Each registered method's most recently trained version."""
    rows = (await session.execute(select(ModelRegistry))).scalars().all()
    epoch = datetime.min.replace(tzinfo=UTC)
    best: dict[str, ModelRegistry] = {}
    for row in rows:
        key = (row.last_trained_at or epoch, row.version)
        current = best.get(row.method)
        if current is None or key > (current.last_trained_at or epoch, current.version):
            best[row.method] = row
    return {method: row.version for method, row in best.items()}


async def compute_rolling_metrics(
    session: AsyncSession,
    *,
    window_days: int,
    now: datetime | None = None,
    versions: dict[str, str] | None = None,
    min_samples: int = 0,
) -> dict[str, MethodMetrics]:
    now = now or datetime.now(UTC)
    start = now - timedelta(days=window_days)
    versions = versions if versions is not None else await latest_versions(session)
    if not versions:
        return {}

    fixtures = {
        fx.id: fx
        for fx in (
            await session.execute(
                select(Fixture).where(
                    Fixture.ft_home.is_not(None),
                    Fixture.kickoff_at >= start,
                    Fixture.kickoff_at <= now,
                )
            )
        ).scalars()
    }
    if not fixtures:
        return {}

    preds = (
        (
            await session.execute(
                select(Prediction).where(
                    Prediction.fixture_id.in_(list(fixtures)),
                    Prediction.market == "1x2",
                    tuple_(Prediction.method, Prediction.model_version).in_(list(versions.items())),
                )
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[str, dict[uuid.UUID, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for p in preds:
        grouped[p.method][p.fixture_id][p.outcome] = float(p.probability)
    complete = {
        method: {fid for fid, outcomes in by_fixture.items() if set(_OUTCOMES) <= set(outcomes)}
        for method, by_fixture in grouped.items()
    }
    participants = [m for m, fids in complete.items() if len(fids) >= max(min_samples, 1)]
    if not participants:
        return {}
    common = sorted(set.intersection(*(complete[m] for m in participants)))
    if not common:
        return {}

    y = np.array([_label(fixtures[fid]) for fid in common])
    odds = await closing_quotes(session, [fixtures[fid] for fid in common])
    odds_matrix = np.array(
        [[float(odds.get(fid, {}).get(o, 0.0)) for o in _OUTCOMES] for fid in common]
    )
    baseline = metrics_mod.brier_baseline(y)

    result: dict[str, MethodMetrics] = {}
    for method in sorted(participants):
        probs = np.array([[grouped[method][fid][o] for o in _OUTCOMES] for fid in common])
        brier = metrics_mod.brier_multiclass(probs, y)
        result[method] = MethodMetrics(
            accuracy_pct=metrics_mod.accuracy_pct(brier, baseline),
            brier=brier,
            log_loss=metrics_mod.log_loss(probs, y),
            roi_vs_closing=metrics_mod.roi_vs_closing(probs, y, odds_matrix),
            sample_count=len(y),
            version=versions[method],
        )
    return result
