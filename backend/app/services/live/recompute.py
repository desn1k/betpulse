"""In-play recompute: Dixon-Coles conditioned on the current score + minute.

Triggered after each successful live poll (not on a fixed timer). A recompute is
performed **only when the match state changed** (minute or score) since the last
in-play row — re-polling identical state is a no-op. After writing the new
probabilities it measures the swing versus the previous in-play row and flags a
push when it exceeds the configured threshold.

LightGBM-live follows the same governance rule as Phase 4 training: it runs only
when a champion LightGBM model is registered (populated on the VPS after real
training). Until then the in-play engine is Dixon-Coles alone and the skip is
logged, never silently dropped.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.base import Method, Outcome
from app.ml.dixon_coles import in_play_one_x_two
from app.models.live import LiveUpdate
from app.models.prediction import PredictionLive

logger = logging.getLogger("live.recompute")

LIVE_MODEL_VERSION = "live-dixon-coles"


@dataclass(slots=True)
class BaseRates:
    """Pre-match expected goals + low-score correction for a pairing."""

    lam_home: float
    lam_away: float
    rho: float = -0.05


@dataclass(slots=True)
class RecomputeResult:
    fixture_id: uuid.UUID
    minute: int
    home_score: int
    away_score: int
    probs: dict[str, float]
    changed: bool
    swing: float
    should_push: bool
    live_update_id: int | None


async def get_base_rates(session: AsyncSession, fixture_id: uuid.UUID) -> BaseRates:
    """Return the pre-match rates driving the in-play Dixon-Coles model.

    Phase 5 uses a documented, league-neutral home-advantaged baseline. When a
    champion Dixon-Coles model is registered its fitted attack/defence strengths
    are loaded here without touching call sites — the seam is intentional.
    """
    return BaseRates(lam_home=1.45, lam_away=1.15, rho=-0.05)


async def _latest_live_update(session: AsyncSession, fixture_id: uuid.UUID) -> LiveUpdate | None:
    stmt = (
        select(LiveUpdate)
        .where(LiveUpdate.fixture_id == fixture_id)
        .order_by(LiveUpdate.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _max_swing(prev: dict[str, float] | None, current: dict[str, float]) -> float:
    if not prev:
        return 0.0
    return max(abs(current[k] - prev.get(k, 0.0)) for k in current)


async def recompute_fixture(
    session: AsyncSession,
    *,
    fixture_id: uuid.UUID,
    minute: int,
    home_score: int,
    away_score: int,
    base_rates: BaseRates,
    swing_threshold: float,
    now: datetime | None = None,
) -> RecomputeResult:
    now = now or datetime.now(tz=UTC)
    latest = await _latest_live_update(session, fixture_id)

    if latest is not None and (latest.minute, latest.home_score, latest.away_score) == (
        minute,
        home_score,
        away_score,
    ):
        logger.debug("recompute skipped: unchanged state for %s", fixture_id)
        return RecomputeResult(
            fixture_id=fixture_id,
            minute=minute,
            home_score=home_score,
            away_score=away_score,
            probs={str(k): v for k, v in latest.payload.get("probs", {}).items()},
            changed=False,
            swing=0.0,
            should_push=False,
            live_update_id=latest.id,
        )

    raw = in_play_one_x_two(
        base_rates.lam_home,
        base_rates.lam_away,
        base_rates.rho,
        home_score,
        away_score,
        minute,
    )
    probs = {str(outcome): raw[outcome] for outcome in (Outcome.home, Outcome.draw, Outcome.away)}

    await _write_predictions_live(session, fixture_id, minute, now, probs)

    prev_probs = latest.payload.get("probs") if latest is not None else None
    swing = _max_swing(prev_probs, probs)
    should_push = latest is not None and swing > swing_threshold

    payload = {
        "fixture_id": str(fixture_id),
        "minute": minute,
        "home_score": home_score,
        "away_score": away_score,
        "probs": probs,
        "model_version": LIVE_MODEL_VERSION,
        "recorded_at": now.isoformat(),
    }
    event = LiveUpdate(
        fixture_id=fixture_id,
        minute=minute,
        home_score=home_score,
        away_score=away_score,
        payload=payload,
    )
    session.add(event)
    await session.flush()

    return RecomputeResult(
        fixture_id=fixture_id,
        minute=minute,
        home_score=home_score,
        away_score=away_score,
        probs=probs,
        changed=True,
        swing=swing,
        should_push=should_push,
        live_update_id=event.id,
    )


async def _write_predictions_live(
    session: AsyncSession,
    fixture_id: uuid.UUID,
    minute: int,
    now: datetime,
    probs: dict[str, float],
) -> None:
    for outcome, prob in probs.items():
        session.add(
            PredictionLive(
                fixture_id=fixture_id,
                method=Method.dixon_coles.value,
                market="1x2",
                outcome=outcome,
                minute=minute,
                recorded_at=now,
                probability=Decimal(str(round(prob, 5))),
            )
        )
    await session.flush()
