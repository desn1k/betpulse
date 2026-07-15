"""Backtest engine (spec §6): matched count, win-rate, ROI on closing odds,
equity curve, max drawdown, Wilson CI, per-league/season breakdown, walk-forward.

All filter values reach the query as **bound parameters** via ORM comparisons —
never string-interpolated — so a SQL fragment in a string filter is treated as a
literal, not executable SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from scipy.stats import norm
from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backtester import BacktestFeature
from app.schemas.backtester import (
    BacktestResult,
    BetType,
    Breakdown,
    FoldResult,
    RunRequest,
    StrategyFilter,
    WilsonInterval,
)

SMALL_SAMPLE_THRESHOLD = 100
_OU_LINE = 2.5

# Odds column name on BacktestFeature for each pick.
_PICK_ODDS_COL = {
    "home": "odds_home",
    "draw": "odds_draw",
    "away": "odds_away",
    "over": "odds_over",
    "under": "odds_under",
}


def _dataset_conditions(filters: StrategyFilter) -> list[ColumnElement[bool]]:
    """Whitelisted filter → bound-parameter conditions (no string interpolation)."""
    conds: list[ColumnElement[bool]] = []
    if filters.league is not None:
        conds.append(BacktestFeature.league_code == filters.league)
    if filters.season is not None:
        conds.append(BacktestFeature.season == filters.season)
    if filters.elo_diff_min is not None:
        conds.append(BacktestFeature.elo_diff >= filters.elo_diff_min)
    if filters.elo_diff_max is not None:
        conds.append(BacktestFeature.elo_diff <= filters.elo_diff_max)
    if filters.avg_total_min is not None:
        conds.append(BacktestFeature.avg_total >= filters.avg_total_min)
    if filters.avg_total_max is not None:
        conds.append(BacktestFeature.avg_total <= filters.avg_total_max)
    if filters.rest_days_min is not None:
        conds.append(
            and_(
                BacktestFeature.rest_days_home >= filters.rest_days_min,
                BacktestFeature.rest_days_away >= filters.rest_days_min,
            )
        )
    return conds


def wilson_interval(wins: int, n: int, confidence: float = 0.95) -> WilsonInterval:
    """Wilson score interval for a binomial proportion at ``confidence`` (95% default).

    With p̂ = wins/n and z the standard-normal quantile for (1+confidence)/2
    (z ≈ 1.96 at 95%)::

        center = (p̂ + z²/2n) / (1 + z²/n)
        half   = (z / (1 + z²/n)) * sqrt( p̂(1-p̂)/n + z²/4n² )
        CI     = [center - half, center + half]
    """
    if n == 0:
        return WilsonInterval(lower=0.0, upper=0.0, confidence=confidence)
    z = float(norm.ppf((1 + confidence) / 2))
    p = wins / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = (z / denom) * ((p * (1 - p) / n + z**2 / (4 * n**2)) ** 0.5)
    return WilsonInterval(
        lower=round(max(0.0, center - half), 4),
        upper=round(min(1.0, center + half), 4),
        confidence=confidence,
    )


@dataclass
class _Bet:
    kickoff_at: datetime
    home_team: str
    away_team: str
    league_code: str
    season: str
    odds: float
    won: bool
    pnl: float


def _settle(row: BacktestFeature, bet_type: BetType, pick: str, odds: float) -> _Bet:
    if bet_type is BetType.x12:
        result = (
            "home"
            if row.ft_home > row.ft_away
            else ("draw" if row.ft_home == row.ft_away else "away")
        )
        won = result == pick
    else:  # total over/under 2.5
        over = row.total_goals > _OU_LINE
        won = (pick == "over" and over) or (pick == "under" and not over)
    pnl = (odds - 1.0) if won else -1.0
    return _Bet(
        kickoff_at=row.kickoff_at,
        home_team=row.home_team,
        away_team=row.away_team,
        league_code=row.league_code,
        season=row.season,
        odds=odds,
        won=won,
        pnl=round(pnl, 4),
    )


def _collect_bets(rows: list[BacktestFeature], request: RunRequest) -> list[_Bet]:
    """Settle the picked selection for each bettable row (odds present + in range)."""
    odds_col_name = _PICK_ODDS_COL[request.pick]
    bets: list[_Bet] = []
    for row in rows:
        price: Decimal | None = getattr(row, odds_col_name)
        if price is None:
            continue
        odds = float(price)
        if request.filters.odds_min is not None and odds < request.filters.odds_min:
            continue
        if request.filters.odds_max is not None and odds > request.filters.odds_max:
            continue
        bets.append(_settle(row, request.bet_type, request.pick, odds))
    return bets


def _max_drawdown(pnls: list[float]) -> tuple[list[float], float]:
    equity: list[float] = []
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in pnls:
        running += x
        equity.append(round(running, 4))
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return equity, round(max_dd, 4)


def _roi(bets: list[_Bet]) -> float:
    if not bets:
        return 0.0
    staked = float(len(bets))
    returned = sum((b.pnl + 1.0) for b in bets)  # payout = stake + pnl
    return round((returned - staked) / staked, 4)


def _breakdown(bets: list[_Bet], key: str) -> list[Breakdown]:
    groups: dict[str, list[_Bet]] = {}
    for b in bets:
        groups.setdefault(getattr(b, key), []).append(b)
    return [Breakdown(key=k, matched_count=len(v), roi=_roi(v)) for k, v in sorted(groups.items())]


def _available_bet_types(rows: list[BacktestFeature]) -> list[BetType]:
    available: list[BetType] = []
    if any(r.odds_home and r.odds_draw and r.odds_away for r in rows):
        available.append(BetType.x12)
    if any(r.odds_over and r.odds_under for r in rows):
        available.append(BetType.total)
    return available


async def _fetch_rows(session: AsyncSession, request: RunRequest) -> list[BacktestFeature]:
    return list(
        (
            await session.execute(
                select(BacktestFeature)
                .where(*_dataset_conditions(request.filters))
                .order_by(BacktestFeature.kickoff_at.asc(), BacktestFeature.id.asc())
            )
        )
        .scalars()
        .all()
    )


async def backtest_bets(session: AsyncSession, request: RunRequest) -> list[_Bet]:
    """Per-bet detail (used by the CSV export)."""
    return _collect_bets(await _fetch_rows(session, request), request)


async def run_backtest(
    session: AsyncSession, request: RunRequest, *, walk_forward: bool = False
) -> BacktestResult:
    rows = await _fetch_rows(session, request)
    bets = _collect_bets(rows, request)

    matched = len(bets)
    wins = sum(1 for b in bets if b.won)
    equity, max_dd = _max_drawdown([b.pnl for b in bets])
    total_staked = float(matched)
    total_return = round(sum((b.pnl + 1.0) for b in bets), 4)

    result = BacktestResult(
        bet_type=request.bet_type,
        pick=request.pick,
        matched_count=matched,
        win_count=wins,
        win_rate=round(wins / matched, 4) if matched else 0.0,
        roi=_roi(bets),
        total_staked=total_staked,
        total_return=total_return,
        equity_curve=equity,
        max_drawdown=max_dd,
        win_rate_ci=wilson_interval(wins, matched),
        by_league=_breakdown(bets, "league_code"),
        by_season=_breakdown(bets, "season"),
        available_bet_types=_available_bet_types(rows),
        small_sample_warning=matched < SMALL_SAMPLE_THRESHOLD,
        walk_forward=walk_forward,
    )

    if walk_forward:
        _apply_walk_forward(result, bets)
    return result


def _apply_walk_forward(result: BacktestResult, bets: list[_Bet]) -> None:
    """Chronological season split. Each season is an out-of-sample test fold
    trained only on strictly-earlier seasons; the first season has no prior data
    and is the in-sample warm-up (never a test fold). ``out_of_sample_roi`` is the
    ROI over all test folds combined."""
    seasons = sorted({b.season for b in bets})
    folds: list[FoldResult] = []
    oos_bets: list[_Bet] = []
    for i, season in enumerate(seasons):
        if i == 0:
            continue  # no earlier seasons to train on → warm-up only
        fold_bets = [b for b in bets if b.season == season]
        oos_bets.extend(fold_bets)
        folds.append(FoldResult(season=season, matched_count=len(fold_bets), roi=_roi(fold_bets)))
    result.folds = folds
    result.out_of_sample_roi = _roi(oos_bets) if oos_bets else None
