"""Read-only data-quality report: coverage rows and every issue type."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.cli import _data_report
from app.core.db import _write_engine
from app.models.fixture import Fixture, FixtureStatus
from app.models.market import Odds
from app.models.reference import League, Team
from app.services.data_quality import ERROR, WARNING, DataReport, build_report
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
SEASON_START = datetime(2025, 8, 9, 14, 0, tzinfo=UTC)  # over: last match well before NOW
N_TEAMS = 16


def _odds(
    fixture: Fixture,
    market: str,
    prices: dict[str, str],
    *,
    ts: datetime | None = None,
    bookmaker: str = "pinnacle",
    closing: bool = True,
) -> list[Odds]:
    return [
        Odds(
            fixture_id=fixture.id,
            bookmaker=bookmaker,
            market=market,
            outcome=outcome,
            ts=ts or fixture.kickoff_at,
            price=Decimal(price),
            is_closing=closing,
        )
        for outcome, price in prices.items()
    ]


async def _seed_clean_season(
    session: AsyncSession, *, code: str = "EPL", season: str = "2025-2026"
) -> tuple[League, list[Team], list[Fixture]]:
    """A complete double round-robin of 16 teams with sane scores and a full
    closing 1X2 + O/U 2.5 Pinnacle snapshot at kickoff — zero issues."""
    league = League(code=code, name=code)
    teams = [
        Team(name=f"T{i}", normalized_name=f"t{i}-{uuid.uuid4().hex[:6]}") for i in range(N_TEAMS)
    ]
    session.add_all([league, *teams])
    await session.flush()
    fixtures: list[Fixture] = []
    slot = 0
    for home in teams:
        for away in teams:
            if home is away:
                continue
            fixtures.append(
                Fixture(
                    league_id=league.id,
                    season=season,
                    home_team_id=home.id,
                    away_team_id=away.id,
                    kickoff_at=SEASON_START + timedelta(hours=12 * slot),
                    status=FixtureStatus.finished,
                    ft_home=2,
                    ft_away=1,
                    ht_home=1,
                    ht_away=0,
                )
            )
            slot += 1
    session.add_all(fixtures)
    await session.flush()
    for fx in fixtures:
        session.add_all(_odds(fx, "1x2", {"home": "2.10", "draw": "3.40", "away": "3.60"}))
        session.add_all(_odds(fx, "ou_2.5", {"over": "1.95", "under": "1.90"}))
    await session.flush()
    return league, teams, fixtures


def _codes(report: DataReport, severity: str | None = None) -> set[str]:
    return {i.code for i in report.issues if severity is None or i.severity == severity}


@pytest.mark.asyncio
async def test_clean_season_has_full_coverage_and_no_issues(session: AsyncSession) -> None:
    _, _, fixtures = await _seed_clean_season(session)
    report = await build_report(session, now=NOW)

    assert report.issues == []
    assert report.exit_code(strict=True) == 0
    (row,) = report.rows
    assert (row.league, row.season, row.fixtures, row.expected_fixtures, row.teams) == (
        "EPL",
        "2025-2026",
        len(fixtures),
        N_TEAMS * (N_TEAMS - 1),
        N_TEAMS,
    )
    assert row.with_score_pct == 100.0
    assert row.time_known_pct == 100.0
    assert row.closing_1x2_by_bookmaker == {"pinnacle": len(fixtures)}
    assert row.closing_ou == len(fixtures)


@pytest.mark.asyncio
async def test_score_errors(session: AsyncSession) -> None:
    _, _, fixtures = await _seed_clean_season(session)
    fixtures[0].ft_home = None
    fixtures[1].ft_home = -1
    fixtures[2].ft_away = 16
    fixtures[3].ht_home, fixtures[3].ft_home = 3, 2
    fixtures[4].kickoff_at = NOW + timedelta(days=2)
    await session.flush()

    report = await build_report(session, now=NOW)

    errors = {i.code: i for i in report.errors}
    assert {"finished_without_score", "impossible_score", "finished_in_future"} <= set(errors)
    impossible = [i for i in report.errors if i.code == "impossible_score"]
    assert {i.fixture_ids[0] for i in impossible} == {str(fixtures[n].id) for n in (1, 2, 3)}
    assert report.exit_code() == 1


@pytest.mark.asyncio
async def test_duplicate_across_season_labels_is_an_error_even_when_scoped(
    session: AsyncSession,
) -> None:
    league, teams, fixtures = await _seed_clean_season(session)
    original = fixtures[10]
    # The same match from a live provider: season "2025", kickoff 35h later.
    session.add(
        Fixture(
            league_id=league.id,
            season="2025",
            home_team_id=original.home_team_id,
            away_team_id=original.away_team_id,
            kickoff_at=original.kickoff_at + timedelta(hours=35),
            status=FixtureStatus.finished,
            ft_home=2,
            ft_away=1,
        )
    )
    await session.flush()

    for seasons in (None, ["2025-2026"], ["2025"]):
        report = await build_report(session, seasons=seasons, now=NOW)
        duplicates = [i for i in report.errors if i.code == "duplicate_fixture"]
        assert len(duplicates) == 1, seasons
        assert str(original.id) in duplicates[0].fixture_ids


@pytest.mark.asyncio
async def test_same_pairing_37h_apart_is_not_a_duplicate(session: AsyncSession) -> None:
    league, _, fixtures = await _seed_clean_season(session)
    original = fixtures[10]
    session.add(
        Fixture(
            league_id=league.id,
            season="2025-2026",
            home_team_id=original.home_team_id,
            away_team_id=original.away_team_id,
            kickoff_at=original.kickoff_at + timedelta(hours=37),
            status=FixtureStatus.finished,
            ft_home=0,
            ft_away=0,
        )
    )
    await session.flush()
    report = await build_report(session, now=NOW)
    assert "duplicate_fixture" not in _codes(report)


@pytest.mark.asyncio
async def test_odds_errors_and_warnings(session: AsyncSession) -> None:
    _, _, fixtures = await _seed_clean_season(session)
    await session.execute(
        update(Odds)
        .where(Odds.fixture_id == fixtures[0].id, Odds.outcome == "home")
        .values(price=Decimal("1.000"))
    )
    # A "closing" quote dated after kickoff is really an in-play price.
    session.add_all(
        _odds(
            fixtures[1],
            "1x2",
            {"home": "1.50", "draw": "4.00", "away": "6.00"},
            ts=fixtures[1].kickoff_at + timedelta(minutes=30),
        )
    )
    # Incomplete snapshot (draw missing) and a silly overround from another book.
    session.add_all(
        _odds(fixtures[2], "1x2", {"home": "2.0", "away": "3.0"}, bookmaker="market_avg")
    )
    session.add_all(
        _odds(
            fixtures[3],
            "1x2",
            {"home": "1.20", "draw": "1.30", "away": "1.40"},
            bookmaker="market_avg",
        )
    )
    await session.flush()

    report = await build_report(session, now=NOW)

    assert {"odds_price_out_of_range", "closing_after_kickoff"} <= _codes(report, ERROR)
    assert {"incomplete_odds_snapshot", "overround_out_of_range"} <= _codes(report, WARNING)
    (row,) = report.rows
    assert row.closing_1x2_by_bookmaker["market_avg"] == 1  # only the complete snapshot


@pytest.mark.asyncio
async def test_season_level_warnings(session: AsyncSession) -> None:
    _, _, fixtures = await _seed_clean_season(session)
    # One fixture lost, 30 without odds, a date-only kickoff.
    await session.execute(delete(Fixture).where(Fixture.id == fixtures[0].id))
    await session.execute(delete(Odds).where(Odds.fixture_id.in_([fx.id for fx in fixtures[1:31]])))
    # Date-only kickoff: its quotes move with it (still at kickoff, not after).
    midnight = fixtures[40].kickoff_at.replace(hour=0, minute=0)
    await session.execute(
        update(Odds).where(Odds.fixture_id == fixtures[40].id).values(ts=midnight)
    )
    fixtures[40].kickoff_at = midnight
    await session.flush()

    report = await build_report(session, now=NOW)

    assert {"season_incomplete", "uneven_team_schedule", "odds_coverage_low"} <= _codes(
        report, WARNING
    )
    assert report.errors == []
    assert report.exit_code() == 0
    assert report.exit_code(strict=True) == 1
    (row,) = report.rows
    assert row.time_known == row.fixtures - 1


@pytest.mark.asyncio
async def test_running_season_is_not_reported_incomplete(session: AsyncSession) -> None:
    _, _, fixtures = await _seed_clean_season(session, season="2026-2027")
    await session.execute(delete(Fixture).where(Fixture.id == fixtures[-1].id))
    await session.flush()
    # The last match was days ago: the season is still running, so a missing
    # fixture is expected, not a gap.
    report = await build_report(session, now=fixtures[-2].kickoff_at + timedelta(days=5))
    assert "season_incomplete" not in _codes(report)
    assert "uneven_team_schedule" not in _codes(report)


@pytest.mark.asyncio
async def test_label_and_team_count_warnings(session: AsyncSession) -> None:
    league = League(code="RPL", name="RPL")
    teams = [Team(name=f"R{i}", normalized_name=f"r{i}-{uuid.uuid4().hex[:6]}") for i in range(3)]
    session.add_all([league, *teams])
    await session.flush()
    session.add(
        Fixture(
            league_id=league.id,
            season="2025/26",
            home_team_id=teams[0].id,
            away_team_id=teams[1].id,
            kickoff_at=SEASON_START,
            status=FixtureStatus.finished,
            ft_home=1,
            ft_away=0,
        )
    )
    await session.flush()
    report = await build_report(session, leagues=["RPL"], now=NOW)
    assert {"non_canonical_season", "unusual_team_count"} <= _codes(report, WARNING)


@pytest.mark.asyncio
async def test_scope_filters_rows(session: AsyncSession) -> None:
    await _seed_clean_season(session, code="EPL")
    await _seed_clean_season(session, code="LALIGA")
    report = await build_report(session, leagues=["LALIGA"], now=NOW)
    assert {r.league for r in report.rows} == {"LALIGA"}


@pytest.mark.asyncio
async def test_report_is_read_only(session: AsyncSession) -> None:
    await _seed_clean_season(session)
    before = (
        await session.scalar(select(func.count()).select_from(Fixture)),
        await session.scalar(select(func.count()).select_from(Odds)),
    )
    await build_report(session, now=NOW)
    assert not session.new and not session.dirty and not session.deleted
    after = (
        await session.scalar(select(func.count()).select_from(Fixture)),
        await session.scalar(select(func.count()).select_from(Odds)),
    )
    assert before == after


@pytest.mark.asyncio
async def test_cli_json_output_and_exit_codes(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, fixtures = await _seed_clean_season(session)
    await session.commit()

    assert await _data_report(None, None, as_json=True, strict=False) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"errors": 0, "warnings": 0}
    assert payload["rows"][0]["closing_1x2_by_bookmaker"] == {"pinnacle": len(fixtures)}

    await session.execute(update(Fixture).where(Fixture.id == fixtures[0].id).values(ft_home=None))
    await session.commit()
    assert await _data_report(None, None, as_json=False, strict=False) == 1
    text = capsys.readouterr().out
    assert "finished_without_score" in text
    assert "errors: 1" in text


@pytest.mark.asyncio
async def test_invalid_price_snapshot_is_an_error_but_not_coverage(session: AsyncSession) -> None:
    _, _, fixtures = await _seed_clean_season(session)
    await session.execute(
        update(Odds)
        .where(Odds.fixture_id == fixtures[0].id, Odds.market == "1x2", Odds.outcome == "home")
        .values(price=Decimal("0.500"))
    )
    await session.execute(
        update(Odds)
        .where(Odds.fixture_id == fixtures[1].id, Odds.market == "ou_2.5", Odds.outcome == "over")
        .values(price=Decimal("1.000"))
    )
    await session.flush()

    report = await build_report(session, now=NOW)

    assert "odds_price_out_of_range" in _codes(report, ERROR)
    (row,) = report.rows
    assert row.closing_1x2_by_bookmaker == {"pinnacle": len(fixtures) - 1}
    assert row.closing_ou == len(fixtures) - 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
async def test_queries_never_bind_one_parameter_per_fixture(
    session: AsyncSession, scoped: bool
) -> None:
    """A full-history run has more fixtures than asyncpg's 32,767 bind
    parameters, so no statement may expand fixture ids into parameters."""
    _, _, fixtures = await _seed_clean_season(session)
    params_per_statement: list[int] = []

    def count(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        params_per_statement.append(len(parameters) if parameters else 0)

    engine = _write_engine().sync_engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        await build_report(session, seasons=["2025-2026"] if scoped else None, now=NOW)
    finally:
        event.remove(engine, "before_cursor_execute", count)

    assert params_per_statement
    assert max(params_per_statement) < 10 < len(fixtures)
