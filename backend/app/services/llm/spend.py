"""LLM spend aggregation for the admin dashboard (spec §8, §9).

Two views over ``llm_analyses`` for a trailing window:

* **Daily buckets** — tokens in/out and cost grouped by UTC calendar day.
  Buckets are computed in SQL with an explicit UTC anchor
  (``date_trunc('day', created_at AT TIME ZONE 'UTC')``) so the boundaries are
  deterministic regardless of the server's timezone; tests seed explicit UTC
  timestamps rather than ``datetime.now()``.
* **Top fixtures by cost** — the 20 most expensive fixtures in the window, with
  team/league labels for display.

The current ``daily_token_budget`` (from the singleton config) is returned
alongside so the chart can draw the budget line.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.fixture import Fixture
from app.models.llm import LlmAnalysis
from app.models.reference import League, Team
from app.services.llm.config import get_config

TOP_FIXTURES = 20

# ``created_at`` is timestamptz; converting AT TIME ZONE 'UTC' yields the UTC
# wall-clock, so date_trunc buckets land on UTC calendar-day boundaries. The
# zone name is a bound parameter, not string-built SQL.
_UTC_DAY = func.date_trunc("day", LlmAnalysis.created_at.op("AT TIME ZONE")("UTC"))


@dataclass(frozen=True)
class DailySpend:
    day: date
    tokens_in: int
    tokens_out: int
    cost: Decimal
    count: int


@dataclass(frozen=True)
class FixtureSpend:
    fixture_id: uuid.UUID
    home: str
    away: str
    league: str
    cost: Decimal
    tokens_in: int
    tokens_out: int
    count: int


@dataclass(frozen=True)
class SpendReport:
    days: int
    since: datetime
    daily: list[DailySpend]
    top_fixtures: list[FixtureSpend]
    daily_token_budget: int
    total_cost: Decimal
    total_tokens: int


async def get_spend(session: AsyncSession, *, days: int) -> SpendReport:
    """Aggregate LLM spend over the trailing ``days`` window (UTC day buckets)."""
    since = datetime.now(UTC) - timedelta(days=days)

    daily_rows = (
        await session.execute(
            select(
                _UTC_DAY.label("day"),
                func.coalesce(func.sum(LlmAnalysis.tokens_in), 0).label("tokens_in"),
                func.coalesce(func.sum(LlmAnalysis.tokens_out), 0).label("tokens_out"),
                func.coalesce(func.sum(LlmAnalysis.cost), 0).label("cost"),
                func.count().label("cnt"),
            )
            .where(LlmAnalysis.created_at >= since)
            .group_by(_UTC_DAY)
            .order_by(_UTC_DAY)
        )
    ).all()

    daily = [
        DailySpend(
            day=r.day.date() if isinstance(r.day, datetime) else r.day,
            tokens_in=int(r.tokens_in),
            tokens_out=int(r.tokens_out),
            cost=Decimal(r.cost),
            count=int(r.cnt),
        )
        for r in daily_rows
    ]

    home = aliased(Team)
    away = aliased(Team)
    fixture_rows = (
        await session.execute(
            select(
                LlmAnalysis.fixture_id.label("fixture_id"),
                home.name.label("home"),
                away.name.label("away"),
                League.name.label("league"),
                func.coalesce(func.sum(LlmAnalysis.cost), 0).label("cost"),
                func.coalesce(func.sum(LlmAnalysis.tokens_in), 0).label("tokens_in"),
                func.coalesce(func.sum(LlmAnalysis.tokens_out), 0).label("tokens_out"),
                func.count().label("cnt"),
            )
            .join(Fixture, Fixture.id == LlmAnalysis.fixture_id)
            .join(home, home.id == Fixture.home_team_id)
            .join(away, away.id == Fixture.away_team_id)
            .join(League, League.id == Fixture.league_id)
            .where(LlmAnalysis.created_at >= since)
            .group_by(LlmAnalysis.fixture_id, home.name, away.name, League.name)
            .order_by(func.sum(LlmAnalysis.cost).desc())
            .limit(TOP_FIXTURES)
        )
    ).all()

    top_fixtures = [
        FixtureSpend(
            fixture_id=r.fixture_id,
            home=r.home,
            away=r.away,
            league=r.league,
            cost=Decimal(r.cost),
            tokens_in=int(r.tokens_in),
            tokens_out=int(r.tokens_out),
            count=int(r.cnt),
        )
        for r in fixture_rows
    ]

    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(LlmAnalysis.cost), 0),
                func.coalesce(
                    func.sum(cast(LlmAnalysis.tokens_in + LlmAnalysis.tokens_out, Integer)), 0
                ),
            ).where(LlmAnalysis.created_at >= since)
        )
    ).one()

    config = await get_config(session)
    return SpendReport(
        days=days,
        since=since,
        daily=daily,
        top_fixtures=top_fixtures,
        daily_token_budget=config.daily_token_budget,
        total_cost=Decimal(totals[0]),
        total_tokens=int(totals[1]),
    )
