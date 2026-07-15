"""Daily LLM-analysis ranking (spec §8, owner decision).

Ranks today's scheduled fixtures by ``model_agreement_pct × |edge_vs_market|``
(the matches models agree on *and* disagree with the market are the most
interesting). The result is written to ``fixtures.fixture_llm_rank`` (1 = match
of the day), so the tier gate is a cheap DB lookup rather than a per-request
computation. Recomputed each midnight by an ARQ cron.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import pstdev

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.base import Method
from app.models.fixture import Fixture, FixtureStatus
from app.models.model_registry import ModelRegistry
from app.models.prediction import Prediction

_MAX_HOME_PROB_STD = 0.5
_EXCLUDED = {Method.consensus.value, Method.market.value}


def _agreement(home_probs: list[float]) -> float:
    """Method agreement on the home-win probability, 0–100 (see matches API)."""
    if len(home_probs) < 2:
        return 0.0
    std = pstdev(home_probs)
    return max(0.0, min(100.0, 100.0 * (1.0 - std / _MAX_HOME_PROB_STD)))


async def _visible_methods(session: AsyncSession) -> set[str]:
    stmt = select(ModelRegistry.method).where(ModelRegistry.is_visible.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    return set(rows)


async def compute_scores(
    session: AsyncSession, fixture_ids: list[uuid.UUID]
) -> dict[uuid.UUID, float]:
    """confidence × edge score per fixture (agreement% × |consensus−market home|)."""
    if not fixture_ids:
        return {}
    visible = await _visible_methods(session)
    rows = (
        (
            await session.execute(
                select(Prediction)
                .where(Prediction.fixture_id.in_(fixture_ids), Prediction.market == "1x2")
                .order_by(Prediction.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    # latest home prob per (fixture, method)
    home: dict[uuid.UUID, dict[str, float]] = defaultdict(dict)
    for p in rows:
        if p.outcome == "home":
            home[p.fixture_id].setdefault(p.method, float(p.probability))

    scores: dict[uuid.UUID, float] = {}
    for fid, by_method in home.items():
        consensus = by_method.get(Method.consensus.value)
        market = by_method.get(Method.market.value)
        if consensus is None:
            continue
        method_probs = [v for m, v in by_method.items() if m not in _EXCLUDED and m in visible]
        edge = abs(consensus - market) if market is not None else 0.0
        scores[fid] = _agreement(method_probs) * edge
    return scores


async def rank_today_fixtures(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Reset and recompute today's ranks. Returns how many fixtures were ranked."""
    now = now or datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    # Clear yesterday's ranks so only today's scheduled fixtures carry a rank.
    await session.execute(
        update(Fixture)
        .where(Fixture.fixture_llm_rank.is_not(None))
        .values(fixture_llm_rank=None)
    )

    fixtures = (
        (
            await session.execute(
                select(Fixture.id).where(
                    Fixture.status == FixtureStatus.scheduled,
                    Fixture.kickoff_at >= day_start,
                    Fixture.kickoff_at < day_end,
                )
            )
        )
        .scalars()
        .all()
    )
    scores = await compute_scores(session, list(fixtures))
    if not scores:
        await session.flush()
        return 0

    # Rank by score desc; ties broken by fixture id for determinism.
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], str(kv[0])))
    for rank, (fid, _score) in enumerate(ordered, start=1):
        await session.execute(
            update(Fixture).where(Fixture.id == fid).values(fixture_llm_rank=rank)
        )
    await session.flush()
    return len(ordered)
