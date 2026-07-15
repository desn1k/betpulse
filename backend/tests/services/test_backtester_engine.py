"""Backtest engine + feature-store population + SQL-injection safety (spec §6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.models.backtester import BacktestFeature
from app.models.fixture import Fixture, FixtureStatus
from app.models.reference import League, Team
from app.providers.football_data_couk import FootballDataCoUkProvider
from app.schemas.backtester import BetType, RunRequest, StrategyFilter
from app.services.backtester.engine import run_backtest, wilson_interval
from app.services.backtester.population import populate_backtest_features
from app.services.ingestion.football_data import ingest_dtos
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURE = Path(__file__).parent.parent / "fixtures" / "football_data" / "E0_2324.csv"


class _Seeder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.league: League | None = None
        self.home: Team | None = None
        self.away: Team | None = None
        self._n = 0

    async def setup(self, league_code: str = "EPL") -> None:
        self.league = League(code=league_code, name=league_code)
        self.home = Team(name="Home FC", normalized_name=f"home-{uuid.uuid4().hex[:6]}")
        self.away = Team(name="Away FC", normalized_name=f"away-{uuid.uuid4().hex[:6]}")
        self.session.add_all([self.league, self.home, self.away])
        await self.session.flush()

    async def feature(
        self,
        *,
        ft_home: int,
        ft_away: int,
        season: str = "2023-2024",
        elo_diff: float = 0.0,
        odds_home: str | None = "2.00",
        odds_draw: str | None = "3.40",
        odds_away: str | None = "3.60",
        odds_over: str | None = "1.90",
        odds_under: str | None = "1.95",
    ) -> None:
        assert self.league and self.home and self.away
        self._n += 1
        ko = datetime(2023, 8, 1, tzinfo=UTC) + timedelta(days=self._n)
        fx = Fixture(
            league_id=self.league.id,
            season=season,
            home_team_id=self.home.id,
            away_team_id=self.away.id,
            kickoff_at=ko,
            status=FixtureStatus.finished,
            ft_home=ft_home,
            ft_away=ft_away,
        )
        self.session.add(fx)
        await self.session.flush()
        self.session.add(
            BacktestFeature(
                fixture_id=fx.id,
                league_id=self.league.id,
                league_code=self.league.code,
                season=season,
                kickoff_at=ko,
                home_team_id=self.home.id,
                away_team_id=self.away.id,
                home_team=self.home.name,
                away_team=self.away.name,
                ft_home=ft_home,
                ft_away=ft_away,
                total_goals=ft_home + ft_away,
                elo_home=1500,
                elo_away=1500,
                elo_diff=elo_diff,
                rolling_xg_home=1.4,
                rolling_xg_away=1.3,
                avg_total=2.7,
                rest_days_home=7,
                rest_days_away=7,
                form_home=1.5,
                form_away=1.5,
                odds_home=None if odds_home is None else Decimal(odds_home),
                odds_draw=None if odds_draw is None else Decimal(odds_draw),
                odds_away=None if odds_away is None else Decimal(odds_away),
                odds_over=None if odds_over is None else Decimal(odds_over),
                odds_under=None if odds_under is None else Decimal(odds_under),
            )
        )
        await self.session.flush()


def _req(bet_type: BetType, pick: str, **filters: object) -> RunRequest:
    return RunRequest(bet_type=bet_type, pick=pick, filters=StrategyFilter(**filters))


def test_wilson_interval_95() -> None:
    ci = wilson_interval(1, 3)
    assert ci.confidence == 0.95
    assert 0.0 <= ci.lower <= 1 / 3 <= ci.upper <= 1.0


@pytest.mark.asyncio
async def test_roi_equity_and_drawdown(session: AsyncSession) -> None:
    s = _Seeder(session)
    await s.setup()
    await s.feature(ft_home=2, ft_away=0, odds_home="2.00")  # home win → +1.0
    await s.feature(ft_home=0, ft_away=1, odds_home="1.50")  # away win → -1.0
    await s.feature(ft_home=1, ft_away=1, odds_home="3.00")  # draw     → -1.0
    await session.commit()

    result = await run_backtest(session, _req(BetType.x12, "home"))
    assert result.matched_count == 3
    assert result.win_count == 1
    assert result.win_rate == pytest.approx(0.3333, abs=1e-3)
    assert result.total_return == pytest.approx(2.0)  # one 2.00 payout
    assert result.roi == pytest.approx(-0.3333, abs=1e-3)
    assert result.equity_curve == [1.0, 0.0, -1.0]
    assert result.max_drawdown == pytest.approx(2.0)
    assert result.roi_disclaimer is True
    assert result.small_sample_warning is True  # 3 < 100
    assert BetType.x12 in result.available_bet_types
    assert BetType.total in result.available_bet_types


@pytest.mark.asyncio
async def test_total_over_under_bet(session: AsyncSession) -> None:
    s = _Seeder(session)
    await s.setup()
    await s.feature(ft_home=3, ft_away=1, odds_over="1.80")  # 4 goals → over wins +0.8
    await s.feature(ft_home=0, ft_away=1, odds_over="2.00")  # 1 goal  → over loses -1.0
    await session.commit()

    result = await run_backtest(session, _req(BetType.total, "over"))
    assert result.matched_count == 2
    assert result.win_count == 1
    # returns: 1.80 (win) + 0 (loss) = 1.80 on 2 staked → ROI = (1.80-2)/2 = -0.10
    assert result.roi == pytest.approx(-0.1, abs=1e-4)


@pytest.mark.asyncio
async def test_available_bet_types_reflects_missing_odds(session: AsyncSession) -> None:
    s = _Seeder(session)
    await s.setup()
    # Only 1X2 odds present → totals not available for this dataset.
    await s.feature(ft_home=1, ft_away=0, odds_over=None, odds_under=None)
    await session.commit()
    result = await run_backtest(session, _req(BetType.x12, "home"))
    assert result.available_bet_types == [BetType.x12]


@pytest.mark.asyncio
async def test_walk_forward_splits_by_season(session: AsyncSession) -> None:
    s = _Seeder(session)
    await s.setup()
    # Season 1 (warm-up) then season 2 (out-of-sample test fold).
    await s.feature(ft_home=2, ft_away=0, season="2022-2023", odds_home="2.00")
    await s.feature(ft_home=1, ft_away=0, season="2023-2024", odds_home="2.50")
    await session.commit()

    result = await run_backtest(session, _req(BetType.x12, "home"), walk_forward=True)
    assert result.walk_forward is True
    # Only season 2 is a test fold; season 1 is warm-up.
    assert [f.season for f in result.folds] == ["2023-2024"]
    assert result.out_of_sample_roi == pytest.approx(1.5)  # single 2.50 winner


@pytest.mark.asyncio
async def test_sql_injection_string_is_bound_not_executed(session: AsyncSession) -> None:
    """A SQL fragment in a string filter must be treated as a literal (bound
    parameter), verified by actually running the query against Postgres."""
    s = _Seeder(session)
    await s.setup(league_code="EPL")
    await s.feature(ft_home=2, ft_away=0)
    await session.commit()

    # Sanity: a legitimate filter returns the row.
    ok = await run_backtest(session, _req(BetType.x12, "home", league="EPL"))
    assert ok.matched_count == 1

    # Injection attempt: if interpolated, `OR '1'='1'` would return all rows.
    # As a bound parameter it matches no league_code → 0 rows, and does not error.
    inj = await run_backtest(session, _req(BetType.x12, "home", league="EPL' OR '1'='1"))
    assert inj.matched_count == 0

    # The table is intact (nothing dropped/altered by the injection attempt).
    assert await session.scalar(select(func.count()).select_from(BacktestFeature)) == 1


@pytest.mark.asyncio
async def test_population_is_idempotent(session: AsyncSession) -> None:
    dtos = FootballDataCoUkProvider().parse_csv(FIXTURE.read_bytes(), "EPL", "2023-2024")
    await ingest_dtos(session, dtos, league_code="EPL")
    await session.flush()

    first = await populate_backtest_features(session)
    await session.commit()
    second = await populate_backtest_features(session)
    await session.commit()

    assert first == 10  # 10 finished fixtures
    assert second == 10  # upsert, not duplicate
    total = await session.scalar(select(func.count()).select_from(BacktestFeature))
    assert total == 10
    # Closing odds (1X2 + over/under) are captured on the store.
    row = (await session.execute(select(BacktestFeature).limit(1))).scalar_one()
    assert row.odds_home is not None
    assert row.odds_over is not None
