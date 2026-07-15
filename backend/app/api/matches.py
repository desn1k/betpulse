"""Public match read endpoints (Phase 6).

Read-only projections over ``fixtures`` / ``predictions`` / ``model_registry``
that power the frontend match list and match card. No authentication: guests
read them too. Tier enforcement (blurring the per-method bars for guests) is
Phase 7 — for now every field is returned plus a ``tier_required`` flag so the
frontend can already render the lock placeholder.

Everything is ORM / bound-param queries; no raw SQL string building. Only
``model_registry.is_visible`` methods are exposed on the card, matching the
public-visibility contract from spec §16.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import pstdev
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis
from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import TierContextDep, get_db, get_redis_dep
from app.ml.base import Method
from app.models.fixture import Fixture, FixtureStatus
from app.models.model_registry import ModelRegistry, ModelStatus
from app.models.prediction import Prediction
from app.models.reference import League, Team
from app.schemas.matches import (
    CardFlags,
    LeagueRef,
    MatchDetail,
    MatchList,
    MatchSummary,
    MethodPrediction,
    Probs1x2,
)
from app.services import tiers as tiers_service
from app.services.limits import LimitExceeded, consume_match_view, match_views_remaining

router = APIRouter(tags=["matches"])

# When a caller exhausts their daily match-view budget, the tier they must reach
# to keep viewing. Pro/expert are unlimited and never reach this map.
_LIMIT_UPGRADE = {tiers_service.GUEST: tiers_service.FREE, tiers_service.FREE: tiers_service.PRO}


def _next_tier_for_limit(tier_name: str) -> str:
    return _LIMIT_UPGRADE.get(tier_name, tiers_service.PRO)


def _card_flags(flags: dict[str, object]) -> CardFlags:
    return CardFlags(
        methods=str(flags.get("methods", "blurred_consensus")),
        per_half_totals=bool(flags.get("per_half_totals", False)),
        live_recompute=bool(flags.get("live_recompute", False)),
    )


# A live fixture whose last successful poll is older than this is flagged
# "data delayed" to the client (stalled polling / provider quota exhaustion).
DATA_DELAYED_AFTER = timedelta(minutes=5)
# Default list window when no explicit date is given: today plus the next 2 days.
DEFAULT_WINDOW_DAYS = 3
# The maximum std dev of values in [0, 1] is 0.5 (half at 0, half at 1).
_MAX_HOME_PROB_STD = 0.5


def _data_delayed(last_polled_at: datetime | None, now: datetime) -> bool:
    return last_polled_at is not None and (now - last_polled_at) > DATA_DELAYED_AFTER


def _model_agreement_pct(home_probs: list[float]) -> float | None:
    """How tightly the methods agree on the home-win probability, as 0–100 %.

    We take the population standard deviation of the per-method home-win
    probabilities. Those live in ``[0, 1]``, whose largest possible std dev is
    ``0.5`` (half the methods at 0, half at 1). We map linearly::

        std == 0.0  -> 100 % agreement (all methods identical)
        std == 0.5  ->   0 % agreement (maximally split)

    i.e. ``agreement = 100 * (1 - std / 0.5)``, clamped to ``[0, 100]``. Returns
    ``None`` when fewer than two methods are available (spread undefined).
    """
    if len(home_probs) < 2:
        return None
    std = pstdev(home_probs)
    agreement = 100.0 * (1.0 - std / _MAX_HOME_PROB_STD)
    return round(max(0.0, min(100.0, agreement)), 1)


def _probs_from_outcomes(outcomes: dict[str, float]) -> Probs1x2 | None:
    if not {"home", "draw", "away"} <= outcomes.keys():
        return None
    return Probs1x2(home=outcomes["home"], draw=outcomes["draw"], away=outcomes["away"])


async def _visible_methods(session: AsyncSession) -> set[str]:
    rows = (
        (
            await session.execute(
                select(ModelRegistry.method).where(ModelRegistry.is_visible.is_(True))
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def _champion(session: AsyncSession) -> tuple[str | None, float | None]:
    row = (
        await session.execute(
            select(ModelRegistry.method, ModelRegistry.accuracy_pct)
            .where(ModelRegistry.status == ModelStatus.champion)
            .order_by(ModelRegistry.accuracy_pct.desc().nullslast())
            .limit(1)
        )
    ).first()
    if row is None:
        return None, None
    method, accuracy = row
    return method, (None if accuracy is None else float(accuracy))


async def _latest_1x2(
    session: AsyncSession, fixture_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, dict[str, float]]]:
    """Latest 1X2 probabilities per (fixture, method, outcome).

    Predictions are versioned; ordering by ``created_at`` descending and keeping
    the first row seen for each (method, outcome) yields the newest model version.
    """
    if not fixture_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Prediction)
                .where(
                    Prediction.fixture_id.in_(fixture_ids),
                    Prediction.market == "1x2",
                )
                .order_by(Prediction.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for p in rows:
        method_map = out[p.fixture_id][p.method]
        method_map.setdefault(p.outcome, float(p.probability))
    return out


def _summary(
    fx: Fixture,
    league: League,
    home: str,
    away: str,
    consensus: Probs1x2 | None,
    champion_method: str | None,
    champion_accuracy_pct: float | None,
    now: datetime,
) -> MatchSummary:
    return MatchSummary(
        id=fx.id,
        league=LeagueRef(code=league.code, name=league.name),
        home_team=home,
        away_team=away,
        kickoff_at=fx.kickoff_at,
        status=fx.status,
        minute=fx.minute,
        home_score=fx.ft_home,
        away_score=fx.ft_away,
        consensus=consensus,
        champion_method=champion_method,
        champion_accuracy_pct=champion_accuracy_pct,
        last_polled_at=fx.last_polled_at,
        data_delayed=_data_delayed(fx.last_polled_at, now),
    )


@router.get("/matches", response_model=MatchList)
async def list_matches(
    session: Annotated[AsyncSession, Depends(get_db)],
    tier_ctx: TierContextDep,
    redis: Annotated[Redis, Depends(get_redis_dep)],
    league: Annotated[str | None, Query(max_length=32)] = None,
    match_status: Annotated[FixtureStatus | None, Query(alias="status")] = None,
    date: Annotated[str | None, Query(description="YYYY-MM-DD")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MatchList:
    now = datetime.now(UTC)

    home_team = aliased(Team)
    away_team = aliased(Team)

    conditions: list[ColumnElement[bool]] = [
        exists().where(Prediction.fixture_id == Fixture.id),
    ]
    if league is not None:
        conditions.append(League.code == league)
    statuses = (
        [match_status]
        if match_status is not None
        else [
            FixtureStatus.scheduled,
            FixtureStatus.live,
        ]
    )
    conditions.append(Fixture.status.in_(statuses))

    if date is not None:
        try:
            day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError as exc:  # noqa: TRY003
            raise HTTPException(
                status_code=422,
                detail="date must be YYYY-MM-DD",
            ) from exc
        window_start, window_end = day_start, day_start + timedelta(days=1)
    else:
        window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = window_start + timedelta(days=DEFAULT_WINDOW_DAYS)
    conditions.append(Fixture.kickoff_at >= window_start)
    conditions.append(Fixture.kickoff_at < window_end)

    base = (
        select(Fixture, League, home_team.name, away_team.name)
        .join(League, League.id == Fixture.league_id)
        .join(home_team, home_team.id == Fixture.home_team_id)
        .join(away_team, away_team.id == Fixture.away_team_id)
        .where(*conditions)
    )

    total = (
        await session.execute(select(func.count()).select_from(base.order_by(None).subquery()))
    ).scalar_one()

    page = (
        await session.execute(
            base.order_by(Fixture.kickoff_at.asc(), Fixture.id.asc()).limit(limit).offset(offset)
        )
    ).all()

    fixture_ids = [row[0].id for row in page]
    latest = await _latest_1x2(session, fixture_ids)
    champion_method, champion_accuracy = await _champion(session)

    items: list[MatchSummary] = []
    for fx, lg, home_name, away_name in page:
        consensus = _probs_from_outcomes(latest.get(fx.id, {}).get(Method.consensus.value, {}))
        items.append(
            _summary(
                fx, lg, home_name, away_name, consensus, champion_method, champion_accuracy, now
            )
        )

    remaining = await match_views_remaining(
        redis, identity=tier_ctx.identity, limit=tier_ctx.tier.matches_per_day(), now=now
    )

    return MatchList(
        items=items, total=total, limit=limit, offset=offset, matches_remaining=remaining
    )


@router.get("/matches/{fixture_id}", response_model=MatchDetail)
async def get_match(
    fixture_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    tier_ctx: TierContextDep,
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> MatchDetail:
    now = datetime.now(UTC)

    # Enforce the per-day match-view budget before doing any work. Guests are
    # counted per client IP, authenticated callers per user id (see get_tier_context).
    try:
        await consume_match_view(
            redis, identity=tier_ctx.identity, limit=tier_ctx.tier.matches_per_day(), now=now
        )
    except LimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"tier_required": _next_tier_for_limit(tier_ctx.tier.name)},
        ) from exc

    home_team = aliased(Team)
    away_team = aliased(Team)

    row = (
        await session.execute(
            select(Fixture, League, home_team.name, away_team.name)
            .join(League, League.id == Fixture.league_id)
            .join(home_team, home_team.id == Fixture.home_team_id)
            .join(away_team, away_team.id == Fixture.away_team_id)
            .where(Fixture.id == fixture_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    fx, lg, home_name, away_name = row

    latest = (await _latest_1x2(session, [fixture_id])).get(fixture_id, {})
    visible = await _visible_methods(session)
    champion_method, champion_accuracy = await _champion(session)

    # model_registry accuracy + consensus weight per method (weight for expert).
    registry = {
        m: (None if acc is None else float(acc), float(weight))
        for m, acc, weight in (
            await session.execute(
                select(
                    ModelRegistry.method,
                    ModelRegistry.accuracy_pct,
                    ModelRegistry.display_weight,
                )
            )
        ).all()
    }

    consensus = _probs_from_outcomes(latest.get(Method.consensus.value, {}))
    market = _probs_from_outcomes(latest.get(Method.market.value, {}))

    show_bars = tier_ctx.tier.shows_method_bars()
    show_weights = tier_ctx.tier.shows_weights()

    # Build the per-method bars (also the source of the agreement spread). The
    # consensus and market benchmark are surfaced on their own, so they are
    # excluded here.
    excluded = {Method.consensus.value, Method.market.value}
    methods: list[MethodPrediction] = []
    home_probs: list[float] = []
    for method, outcomes in latest.items():
        if method in excluded or method not in visible:
            continue
        probs = _probs_from_outcomes(outcomes)
        if probs is None:
            continue
        accuracy, weight = registry.get(method, (None, 0.0))
        methods.append(
            MethodPrediction(
                method=method,
                is_champion=method == champion_method,
                accuracy_pct=accuracy,
                probs=probs,
                weight=weight if show_weights else None,
            )
        )
        home_probs.append(probs.home)
    # Stable, meaningful order: champion first, then by accuracy desc, then name.
    methods.sort(key=lambda m: (not m.is_champion, -(m.accuracy_pct or 0.0), m.method))

    delta_vs_market = (
        None if consensus is None or market is None else round(consensus.home - market.home, 5)
    )

    summary = _summary(
        fx, lg, home_name, away_name, consensus, champion_method, champion_accuracy, now
    )
    return MatchDetail(
        **summary.model_dump(),
        # Per-method bars are gated to pro/expert; guest/free get an empty list
        # plus the flags below (frontend blurs the consensus). Aggregate signals
        # (agreement, delta) are computed from the full set and shown to everyone.
        methods=methods if show_bars else [],
        market=market,
        model_agreement_pct=_model_agreement_pct(home_probs),
        delta_vs_market=delta_vs_market,
        flags=_card_flags(tier_ctx.tier.feature_flags),
    )
