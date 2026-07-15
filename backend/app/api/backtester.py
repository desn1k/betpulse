"""Backtester endpoints (spec §6): run, save/list/delete strategies, CSV export.

Runs are limited per tier (``backtester_runs_per_day``); saving and exporting are
tier-gated feature flags. All enforcement is server-side.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, get_redis_dep
from app.models.backtester import Strategy
from app.schemas.backtester import (
    BacktestResult,
    RunRequest,
    StrategyIn,
    StrategyOut,
)
from app.services.backtester.engine import backtest_bets, run_backtest
from app.services.limits import LimitExceeded, consume_backtester_run
from app.services.tiers import PRO, resolve_tier_context

router = APIRouter(prefix="/backtester", tags=["backtester"])


@router.post("/run", response_model=BacktestResult)
async def run(
    request: RunRequest,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
    walk_forward: Annotated[bool, Query()] = False,
) -> BacktestResult:
    tier = await resolve_tier_context(session, redis, user)
    try:
        await consume_backtester_run(redis, user_id=user.id, limit=tier.backtester_runs_per_day())
    except LimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "backtester_daily_limit", "tier_required": PRO},
        ) from exc
    return await run_backtest(session, request, walk_forward=walk_forward)


@router.post("/strategies", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
async def save_strategy(
    payload: StrategyIn,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> Strategy:
    tier = await resolve_tier_context(session, redis, user)
    if not tier.can_save_strategies():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "saving_requires_upgrade", "tier_required": "expert"},
        )
    strategy = Strategy(
        user_id=user.id,
        name=payload.name,
        filters=payload.filters.model_dump(exclude_none=True),
        bet_type=payload.bet_type.value,
        pick=payload.pick,
    )
    session.add(strategy)
    await session.commit()
    return strategy


@router.get("/strategies", response_model=list[StrategyOut])
async def list_strategies(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[Strategy]:
    return list(
        (
            await session.execute(
                select(Strategy)
                .where(Strategy.user_id == user.id)
                .order_by(Strategy.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


@router.delete("/strategies/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_strategy(
    strategy_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None or strategy.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    await session.delete(strategy)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/strategies/{strategy_id}/export.csv")
async def export_strategy_csv(
    strategy_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> Response:
    tier = await resolve_tier_context(session, redis, user)
    if not tier.can_export():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "export_requires_upgrade", "tier_required": "expert"},
        )
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None or strategy.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")

    request = RunRequest.model_validate(
        {"bet_type": strategy.bet_type, "pick": strategy.pick, "filters": strategy.filters}
    )
    bets = await backtest_bets(session, request)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "date",
            "home_team",
            "away_team",
            "league",
            "season",
            "bet_type",
            "pick",
            "odds",
            "outcome",
            "pnl",
            "cumulative_pnl",
        ]
    )
    cumulative = 0.0
    for b in bets:
        cumulative = round(cumulative + b.pnl, 4)
        writer.writerow(
            [
                b.kickoff_at.date().isoformat(),
                b.home_team,
                b.away_team,
                b.league_code,
                b.season,
                strategy.bet_type,
                strategy.pick,
                f"{b.odds:.2f}",
                "win" if b.won else "loss",
                f"{b.pnl:.4f}",
                f"{cumulative:.4f}",
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="strategy_{strategy.name}.csv"'},
    )
