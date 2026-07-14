"""Feature engineering from stored fixtures.

Builds a time-ordered, leakage-free feature table: each row uses only
information available **before** that fixture's kickoff (running Elo, Glicko+RD,
recent form, rest days, home/away, rolling approximate xG/xGA). The label is the
1X2 outcome (0 home, 1 draw, 2 away).
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.elo import DEFAULT_RATING, Elo
from app.ml.glicko2 import Glicko2, GlickoPlayer, MatchResult
from app.models.fixture import Fixture, FixtureStats


def finished_scores(fx: Fixture) -> tuple[int, int]:
    """Full-time goals as ints (fixtures are pre-filtered to finished)."""
    return int(fx.ft_home or 0), int(fx.ft_away or 0)


FEATURE_COLUMNS: list[str] = [
    "elo_home",
    "elo_away",
    "elo_diff",
    "glicko_home",
    "glicko_away",
    "glicko_rd_home",
    "glicko_rd_away",
    "form_home",
    "form_away",
    "rest_days_home",
    "rest_days_away",
    "rolling_xg_home",
    "rolling_xg_away",
]


def feature_schema() -> dict[str, str]:
    """Column name -> dtype, logged as an MLflow artifact per run."""
    return {c: "float64" for c in FEATURE_COLUMNS}


@dataclass(slots=True)
class _TeamState:
    elo: float = DEFAULT_RATING
    glicko: GlickoPlayer = GlickoPlayer()
    recent_points: deque[int] = None  # type: ignore[assignment]
    recent_xg: deque[float] = None  # type: ignore[assignment]
    last_date: object = None


def _label(ft_home: int, ft_away: int) -> int:
    if ft_home > ft_away:
        return 0
    if ft_home == ft_away:
        return 1
    return 2


async def build_feature_table(session: AsyncSession) -> pd.DataFrame:
    """Return a DataFrame with FEATURE_COLUMNS + fixture_id, kickoff_at, label."""
    rows = (
        (
            await session.execute(
                select(Fixture).where(Fixture.ft_home.is_not(None)).order_by(Fixture.kickoff_at)
            )
        )
        .scalars()
        .all()
    )

    stats_rows = (await session.execute(select(FixtureStats))).scalars().all()
    stats_by_fixture = {s.fixture_id: s for s in stats_rows}

    elo = Elo()
    glicko = Glicko2()
    state: dict[uuid.UUID, _TeamState] = defaultdict(
        lambda: _TeamState(recent_points=deque(maxlen=5), recent_xg=deque(maxlen=5))
    )
    league_xg = 1.35

    records: list[dict[str, Any]] = []
    for fx in rows:
        hs = state[fx.home_team_id]
        as_ = state[fx.away_team_id]
        records.append(
            {
                "fixture_id": fx.id,
                "kickoff_at": fx.kickoff_at,
                "label": _label(*finished_scores(fx)),
                "elo_home": hs.elo,
                "elo_away": as_.elo,
                "elo_diff": hs.elo - as_.elo,
                "glicko_home": hs.glicko.rating,
                "glicko_away": as_.glicko.rating,
                "glicko_rd_home": hs.glicko.rd,
                "glicko_rd_away": as_.glicko.rd,
                "form_home": sum(hs.recent_points) / max(len(hs.recent_points), 1),
                "form_away": sum(as_.recent_points) / max(len(as_.recent_points), 1),
                "rest_days_home": _rest_days(hs.last_date, fx.kickoff_at),
                "rest_days_away": _rest_days(as_.last_date, fx.kickoff_at),
                "rolling_xg_home": _rolling(hs.recent_xg, league_xg),
                "rolling_xg_away": _rolling(as_.recent_xg, league_xg),
            }
        )
        _advance_state(elo, glicko, hs, as_, fx, stats_by_fixture)

    return pd.DataFrame.from_records(records)


def _rest_days(last_date: object, kickoff: object) -> float:
    if last_date is None:
        return 7.0
    return float((kickoff - last_date).days)  # type: ignore[operator]


def _rolling(values: deque[float], league_mean: float) -> float:
    if not values:
        return league_mean
    return sum(values) / len(values)


def _advance_state(
    elo: Elo,
    glicko: Glicko2,
    hs: _TeamState,
    as_: _TeamState,
    fx: Fixture,
    stats: dict[uuid.UUID, FixtureStats],
) -> None:
    ft_home, ft_away = finished_scores(fx)
    new_home, new_away = elo.update(hs.elo, as_.elo, ft_home, ft_away)
    hs.elo, as_.elo = new_home, new_away

    home_score = 1.0 if ft_home > ft_away else (0.5 if ft_home == ft_away else 0.0)
    hs.glicko = glicko.update(
        hs.glicko, [MatchResult(as_.glicko.rating, as_.glicko.rd, home_score)]
    )
    as_.glicko = glicko.update(
        as_.glicko, [MatchResult(hs.glicko.rating, hs.glicko.rd, 1.0 - home_score)]
    )

    hs.recent_points.append(3 if ft_home > ft_away else (1 if ft_home == ft_away else 0))
    as_.recent_points.append(3 if ft_away > ft_home else (1 if ft_home == ft_away else 0))

    st = stats.get(fx.id)
    if st is not None:
        from app.ml.xg import XgModel

        xgm = XgModel(has_coordinates=False)
        if st.home_shots is not None:
            hs.recent_xg.append(
                xgm.approximate_match_xg(st.home_shots or 0, st.home_shots_on_target or 0)
            )
        if st.away_shots is not None:
            as_.recent_xg.append(
                xgm.approximate_match_xg(st.away_shots or 0, st.away_shots_on_target or 0)
            )

    hs.last_date = fx.kickoff_at
    as_.last_date = fx.kickoff_at
