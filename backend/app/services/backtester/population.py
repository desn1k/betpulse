"""Populate the ``backtest_features`` store (spec §6).

Reuses :func:`app.ml.features.build_feature_table` for the leakage-free as-of
features (Elo, rolling xG, rest days, form computed chronologically), joins the
fixture metadata (league, season, teams, scores) and the closing-odds snapshot
(Pinnacle 1X2 + over/under 2.5), and upserts one row per fixture. Idempotent:
re-running updates existing rows (conflict on ``fixture_id``).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.features import build_feature_table
from app.models.backtester import BacktestFeature
from app.models.fixture import Fixture
from app.models.market import Odds
from app.models.reference import League, Team

_OU_MARKET = "ou_2.5"
_X12_MARKET = "1x2"


async def _closing_odds(
    session: AsyncSession, fixture_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Decimal]]:
    """Latest closing price per (fixture, market, outcome) — Pinnacle preferred."""
    out: dict[uuid.UUID, dict[str, Decimal]] = {}
    if not fixture_ids:
        return out
    rows = (
        (
            await session.execute(
                select(Odds).where(
                    Odds.fixture_id.in_(fixture_ids),
                    Odds.market.in_([_X12_MARKET, _OU_MARKET]),
                    Odds.is_closing.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    for o in rows:
        # 1x2 (home/draw/away) and ou_2.5 (over/under) outcomes never collide.
        out.setdefault(o.fixture_id, {}).setdefault(o.outcome, o.price)
    return out


async def populate_backtest_features(session: AsyncSession) -> int:
    """(Re)build the feature store from every finished fixture. Returns rows written."""
    features = await build_feature_table(session)
    if features.empty:
        return 0

    fixture_ids = [row.fixture_id for row in features.itertuples()]
    fixtures = {
        f.id: f
        for f in (
            await session.execute(select(Fixture).where(Fixture.id.in_(fixture_ids)))
        ).scalars()
    }
    leagues = {lg.id: lg for lg in (await session.execute(select(League))).scalars()}
    teams = {t.id: t for t in (await session.execute(select(Team))).scalars()}
    odds = await _closing_odds(session, fixture_ids)

    written = 0
    for r in features.itertuples():
        fx: Fixture | None = fixtures.get(r.fixture_id)
        if fx is None or fx.ft_home is None or fx.ft_away is None:
            continue
        league = leagues.get(fx.league_id)
        home = teams.get(fx.home_team_id)
        away = teams.get(fx.away_team_id)
        if league is None or home is None or away is None:
            continue
        fo = odds.get(fx.id, {})

        stmt = (
            pg_insert(BacktestFeature)
            .values(
                fixture_id=fx.id,
                league_id=league.id,
                league_code=league.code,
                season=fx.season,
                kickoff_at=fx.kickoff_at,
                home_team_id=home.id,
                away_team_id=away.id,
                home_team=home.name,
                away_team=away.name,
                ft_home=fx.ft_home,
                ft_away=fx.ft_away,
                total_goals=fx.ft_home + fx.ft_away,
                ht_home=fx.ht_home,
                ht_away=fx.ht_away,
                elo_home=round(float(r.elo_home), 3),
                elo_away=round(float(r.elo_away), 3),
                elo_diff=round(float(r.elo_diff), 3),
                rolling_xg_home=round(float(r.rolling_xg_home), 3),
                rolling_xg_away=round(float(r.rolling_xg_away), 3),
                avg_total=round(float(r.rolling_xg_home) + float(r.rolling_xg_away), 3),
                rest_days_home=round(float(r.rest_days_home), 2),
                rest_days_away=round(float(r.rest_days_away), 2),
                form_home=round(float(r.form_home), 3),
                form_away=round(float(r.form_away), 3),
                odds_home=fo.get("home"),
                odds_draw=fo.get("draw"),
                odds_away=fo.get("away"),
                odds_over=fo.get("over"),
                odds_under=fo.get("under"),
            )
            .on_conflict_do_update(
                constraint="uq_backtest_feature_fixture",
                set_={
                    "elo_home": round(float(r.elo_home), 3),
                    "elo_away": round(float(r.elo_away), 3),
                    "elo_diff": round(float(r.elo_diff), 3),
                    "rolling_xg_home": round(float(r.rolling_xg_home), 3),
                    "rolling_xg_away": round(float(r.rolling_xg_away), 3),
                    "avg_total": round(float(r.rolling_xg_home) + float(r.rolling_xg_away), 3),
                    "rest_days_home": round(float(r.rest_days_home), 2),
                    "rest_days_away": round(float(r.rest_days_away), 2),
                    "form_home": round(float(r.form_home), 3),
                    "form_away": round(float(r.form_away), 3),
                    "odds_home": fo.get("home"),
                    "odds_draw": fo.get("draw"),
                    "odds_away": fo.get("away"),
                    "odds_over": fo.get("over"),
                    "odds_under": fo.get("under"),
                },
            )
        )
        await session.execute(stmt)
        written += 1
    await session.flush()
    return written
