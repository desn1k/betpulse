"""football-data.co.uk provider (role: historical, odds).

Free CSV archives of European league results with HT/FT scores and closing odds
from 10+ bookmakers. Parsing (``parse_csv``) is separated from the network fetch
(``download_csv``) so the contract test can run fully offline against a committed
CSV slice.

Column mapping and season-format drift are handled in ``_COLUMNS`` /
``_QUOTE_COLUMNS`` — keep ``docs/DATA_SOURCES.md`` in sync with these.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from app.providers.base import (
    BaseProvider,
    Capability,
    DateRange,
    NotSupportedError,
)
from app.providers.dtos import (
    BookmakerOddsDTO,
    FixtureDTO,
    LeagueRef,
    LiveFixtureDTO,
    OddsDTO,
    QuotaDTO,
    StatsDTO,
    TeamRef,
)

CSV_URL_TEMPLATE = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"

# CSV dates and times are UK local time (GMT/BST).
_UK = ZoneInfo("Europe/London")
# A date-only row is stored at noon UTC: noon keeps the calendar date in every
# time zone, and same-day date-only fixtures share one instant (one ML batch).
DATE_ONLY_HOUR_UTC = 12

# Canonical league code -> football-data.co.uk division code.
LEAGUE_CODE_MAP: dict[str, str] = {
    "EPL": "E0",
    "LALIGA": "SP1",
    "SERIEA": "I1",
    "BUNDESLIGA": "D1",
    "LIGUE1": "F1",
}

# Quote columns, each read independently and stored with its own kind:
#   * closing quotes (``...C...`` columns) at ``ts = kickoff``, ``is_closing``;
#   * Pinnacle **pre-closing** quotes (``PSH`` / ``P>2.5`` — collected some time
#     before kickoff) at ``ts = kickoff - PRE_CLOSING_LEAD``, never as closing.
# football-data does not publish when a pre-closing quote was taken (its notes
# say Friday/Tuesday afternoon for weekend/midweek games), so one day before
# kickoff is an approximation. It only orders the pre-closing quote before the
# closing one; the time rule (app.ml.odds_selection) never treats it as closing.
PRE_CLOSING_LEAD = timedelta(days=1)
_OU_25_LINE = "2.5"
# (bookmaker, market, outcome -> column, is_closing)
_QUOTE_COLUMNS: list[tuple[str, str, dict[str, str], bool]] = [
    ("pinnacle", "1x2", {"home": "PSCH", "draw": "PSCD", "away": "PSCA"}, True),
    ("pinnacle", "1x2", {"home": "PSH", "draw": "PSD", "away": "PSA"}, False),
    ("market_avg", "1x2", {"home": "AvgCH", "draw": "AvgCD", "away": "AvgCA"}, True),
    ("pinnacle", f"ou_{_OU_25_LINE}", {"over": "PC>2.5", "under": "PC<2.5"}, True),
    ("pinnacle", f"ou_{_OU_25_LINE}", {"over": "P>2.5", "under": "P<2.5"}, False),
    ("market_avg", f"ou_{_OU_25_LINE}", {"over": "AvgC>2.5", "under": "AvgC<2.5"}, True),
]


def canonical_to_fd_code(league_code: str) -> str:
    try:
        return LEAGUE_CODE_MAP[league_code]
    except KeyError as exc:  # noqa: TRY003
        raise NotSupportedError(
            f"football-data.co.uk has no division for league '{league_code}'"
        ) from exc


def season_to_fd(season: str) -> str:
    """'2023-2024' -> '2324'."""
    start, end = season.split("-")
    return f"{start[2:]}{end[2:]}"


def _to_decimal(value: object) -> Decimal | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _to_int(value: object) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return None


def _strip_comment_lines(content: bytes) -> bytes:
    """Drop leading ``#`` comment lines (used by committed test fixtures; real
    downloads have none)."""
    lines = content.split(b"\n")
    kept = [ln for ln in lines if not ln.lstrip().startswith(b"#")]
    return b"\n".join(kept)


def external_id(division: str, season: str, local_date: datetime, home: str, away: str) -> str:
    """Synthetic, reproducible id for a football-data row (the CSVs have none):
    division, canonical season, the CSV's (UK local) date and normalized team
    names. Migration 0015 back-fills existing rows with the same formula."""
    from app.providers.id_mapping import normalize_name

    return (
        f"fd:{division}:{season}:{local_date:%Y%m%d}:{normalize_name(home)}:{normalize_name(away)}"
    )


class FootballDataCoUkProvider(BaseProvider):
    name = "football_data_couk"
    capabilities = frozenset({Capability.HISTORICAL, Capability.ODDS})
    # No licence for commercial use: dev / local only (the core refuses it in
    # production). It may seed canonical leagues/teams for development data.
    licensed_for_production = False
    may_seed_canonical = True

    def __init__(self, leagues: list[str] | None = None, timeout: float = 60.0) -> None:
        self.leagues = leagues or list(LEAGUE_CODE_MAP)
        self._timeout = timeout

    async def download_csv(self, league_code: str, season: str) -> bytes:
        url = CSV_URL_TEMPLATE.format(
            season=season_to_fd(season), code=canonical_to_fd_code(league_code)
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    async def fetch(self, league_code: str, season: str) -> list[FixtureDTO]:
        """SourceAdapter entry point: download and parse one league/season."""
        return self.parse_csv(await self.download_csv(league_code, season), league_code, season)

    def parse_csv(self, content: bytes, league_code: str, season: str) -> list[FixtureDTO]:
        df = pd.read_csv(
            io.BytesIO(_strip_comment_lines(content)),
            encoding="latin-1",
            on_bad_lines="skip",
        )
        league = LeagueRef(raw_name=league_code, raw_code=canonical_to_fd_code(league_code))
        fixtures: list[FixtureDTO] = []

        for offset, (_, row) in enumerate(df.iterrows(), start=2):  # start=2: 1 header + 1
            home = row.get("HomeTeam")
            away = row.get("AwayTeam")
            if not isinstance(home, str) or not isinstance(away, str):
                continue
            parsed = self._parse_kickoff(row)
            if parsed is None:
                continue
            kickoff, time_known, local_date = parsed

            odds = self._parse_quotes(row, kickoff)
            fixtures.append(
                FixtureDTO(
                    provider=self.name,
                    external_id=external_id(
                        league.raw_code or league_code, season, local_date, home, away
                    ),
                    league=league,
                    season=season,
                    home=TeamRef(raw_name=home.strip()),
                    away=TeamRef(raw_name=away.strip()),
                    kickoff_at=kickoff,
                    kickoff_time_known=time_known,
                    status="finished",
                    ft_home=_to_int(row.get("FTHG")),
                    ft_away=_to_int(row.get("FTAG")),
                    ht_home=_to_int(row.get("HTHG")),
                    ht_away=_to_int(row.get("HTAG")),
                    odds=odds,
                    stats=self._parse_stats(row),
                    source_row=offset,
                )
            )
        return fixtures

    @staticmethod
    def _parse_kickoff(row: pd.Series) -> tuple[datetime, bool, datetime] | None:
        """``(kickoff_utc, time_known, local_date)``. With a ``Time`` column the
        UK local wall-clock is converted to UTC (GMT/BST aware); a date-only row
        gets 12:00 UTC of its date and ``time_known = False``."""
        raw_date = row.get("Date")
        if not isinstance(raw_date, str) or not raw_date.strip():
            return None
        parsed = pd.to_datetime(raw_date, dayfirst=True, errors="coerce")
        if pd.isna(parsed):
            return None
        local_date: datetime = parsed.to_pydatetime().replace(hour=0, minute=0, second=0)
        raw_time = row.get("Time")
        if isinstance(raw_time, str) and ":" in raw_time:
            try:
                hh, mm = raw_time.split(":")[:2]
                local = local_date.replace(hour=int(hh), minute=int(mm), tzinfo=_UK)
                return local.astimezone(UTC), True, local_date
            except ValueError:
                pass
        noon = local_date.replace(hour=DATE_ONLY_HOUR_UTC, tzinfo=UTC)
        return noon, False, local_date

    @staticmethod
    def _parse_quotes(row: pd.Series, kickoff: datetime) -> list[BookmakerOddsDTO]:
        """Every complete quote set in the row (see ``_QUOTE_COLUMNS``)."""
        quotes: list[BookmakerOddsDTO] = []
        for bookmaker, market, columns, is_closing in _QUOTE_COLUMNS:
            prices = {outcome: _to_decimal(row.get(col)) for outcome, col in columns.items()}
            if not all(prices.values()):
                continue
            ts = kickoff if is_closing else kickoff - PRE_CLOSING_LEAD
            quotes.extend(
                BookmakerOddsDTO(
                    bookmaker=bookmaker,
                    market=market,
                    outcome=outcome,
                    price=price,
                    ts=ts,
                    is_closing=is_closing,
                )
                for outcome, price in prices.items()
                if price is not None
            )
        return quotes

    @staticmethod
    def _parse_stats(row: pd.Series) -> StatsDTO | None:
        stats = StatsDTO(
            home_shots=_to_int(row.get("HS")),
            away_shots=_to_int(row.get("AS")),
            home_shots_on_target=_to_int(row.get("HST")),
            away_shots_on_target=_to_int(row.get("AST")),
            home_corners=_to_int(row.get("HC")),
            away_corners=_to_int(row.get("AC")),
        )
        if any(v is not None for v in stats.model_dump().values()):
            return stats
        return None

    async def fetch_fixtures(self, date_range: DateRange) -> list[FixtureDTO]:
        fixtures: list[FixtureDTO] = []
        for season in _seasons_in_range(date_range):
            for league_code in self.leagues:
                if league_code not in LEAGUE_CODE_MAP:
                    continue
                content = await self.download_csv(league_code, season)
                fixtures.extend(self.parse_csv(content, league_code, season))
        return fixtures

    async def fetch_live(self) -> list[LiveFixtureDTO]:
        raise NotSupportedError("football-data.co.uk is historical only")

    async def fetch_odds(self, fixture_id: str) -> OddsDTO:
        raise NotSupportedError("odds are embedded in fetch_fixtures for this provider")

    async def fetch_stats(self, fixture_id: str) -> StatsDTO:
        raise NotSupportedError("stats are embedded in fetch_fixtures for this provider")

    async def rate_limit_state(self) -> QuotaDTO:
        # Free CSVs have no request quota.
        return QuotaDTO(provider=self.name, requests_remaining=10**9)


def _seasons_in_range(date_range: DateRange) -> list[str]:
    """European seasons (Aug–May) overlapping the range, as 'YYYY-YYYY'."""
    seasons: list[str] = []
    for year in range(date_range.start.year - 1, date_range.end.year + 1):
        seasons.append(f"{year}-{year + 1}")
    return seasons
