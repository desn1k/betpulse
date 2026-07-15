"""Contract test: FootballDataCoUkProvider against a committed real CSV slice."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.providers.dtos import FixtureDTO
from app.providers.football_data_couk import FootballDataCoUkProvider

FIXTURE = Path(__file__).parent.parent / "fixtures" / "football_data" / "E0_2324.csv"


def _load() -> list[FixtureDTO]:
    content = FIXTURE.read_bytes()
    return FootballDataCoUkProvider().parse_csv(content, "EPL", "2023-2024")


def test_parses_all_rows_ignoring_comment_header() -> None:
    fixtures = _load()
    assert len(fixtures) == 10
    assert all(f.provider == "football_data_couk" for f in fixtures)
    assert all(f.season == "2023-2024" for f in fixtures)


def test_scores_and_halftime_are_correct() -> None:
    fixtures = _load()
    bur = next(f for f in fixtures if f.home.raw_name == "Burnley")
    assert (bur.ft_home, bur.ft_away) == (0, 3)
    assert (bur.ht_home, bur.ht_away) == (0, 1)
    assert bur.away.raw_name == "Man City"
    assert bur.kickoff_at.year == 2023 and bur.kickoff_at.month == 8 and bur.kickoff_at.day == 11
    assert bur.kickoff_at.hour == 20
    assert bur.kickoff_at.tzinfo is not None


def test_pinnacle_closing_odds_are_extracted() -> None:
    fixtures = _load()
    bur = next(f for f in fixtures if f.home.raw_name == "Burnley")
    assert all(o.bookmaker == "pinnacle" for o in bur.odds)

    x12 = {o.outcome: o for o in bur.odds if o.market == "1x2"}
    assert set(x12) == {"home", "draw", "away"}
    assert x12["home"].price == Decimal("7.50")
    assert x12["away"].price == Decimal("1.45")
    assert x12["home"].is_closing is True

    # Over/under 2.5 closing odds are parsed into the ou_2.5 market.
    ou = {o.outcome: o for o in bur.odds if o.market == "ou_2.5"}
    assert set(ou) == {"over", "under"}
    assert ou["over"].price == Decimal("1.62")
    assert ou["under"].price == Decimal("2.38")


def test_stats_are_extracted() -> None:
    fixtures = _load()
    bur = next(f for f in fixtures if f.home.raw_name == "Burnley")
    assert bur.stats is not None
    assert bur.stats.home_shots == 4
    assert bur.stats.away_shots == 17
