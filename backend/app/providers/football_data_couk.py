"""football-data.co.uk provider (role: historical, odds).

Free CSV archives of European league results with HT/FT scores and closing odds
from 10+ bookmakers. Parsing (``parse_csv``) is separated from the network fetch
(``download_csv``) so the contract test can run fully offline against a committed
CSV slice.

Column mapping and season-format drift are handled in ``_COLUMNS`` /
``_PINNACLE_CLOSING`` — keep ``docs/DATA_SOURCES.md`` in sync with these.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

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

# Canonical league code -> football-data.co.uk division code.
LEAGUE_CODE_MAP: dict[str, str] = {
    "EPL": "E0",
    "LALIGA": "SP1",
    "SERIEA": "I1",
    "BUNDESLIGA": "D1",
    "LIGUE1": "F1",
}

# Pinnacle closing 1X2 columns, newest first; older files used PSH/PSD/PSA.
_PINNACLE_CLOSING: list[tuple[str, str, str]] = [
    ("PSCH", "PSCD", "PSCA"),
    ("PSH", "PSD", "PSA"),
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


class FootballDataCoUkProvider(BaseProvider):
    name = "football_data_couk"
    capabilities = frozenset({Capability.HISTORICAL, Capability.ODDS})

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
            kickoff = self._parse_kickoff(row)
            if kickoff is None:
                continue

            odds = self._parse_pinnacle_closing(row, kickoff)
            fixtures.append(
                FixtureDTO(
                    provider=self.name,
                    league=league,
                    season=season,
                    home=TeamRef(raw_name=home.strip()),
                    away=TeamRef(raw_name=away.strip()),
                    kickoff_at=kickoff,
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
    def _parse_kickoff(row: pd.Series) -> datetime | None:
        raw_date = row.get("Date")
        if not isinstance(raw_date, str) or not raw_date.strip():
            return None
        parsed = pd.to_datetime(raw_date, dayfirst=True, errors="coerce")
        if pd.isna(parsed):
            return None
        dt: datetime = parsed.to_pydatetime()
        raw_time = row.get("Time")
        if isinstance(raw_time, str) and ":" in raw_time:
            try:
                hh, mm = raw_time.split(":")[:2]
                dt = dt.replace(hour=int(hh), minute=int(mm))
            except ValueError:
                pass
        return dt.replace(tzinfo=UTC)

    @staticmethod
    def _parse_pinnacle_closing(row: pd.Series, ts: datetime) -> list[BookmakerOddsDTO]:
        for home_col, draw_col, away_col in _PINNACLE_CLOSING:
            h = _to_decimal(row.get(home_col))
            d = _to_decimal(row.get(draw_col))
            a = _to_decimal(row.get(away_col))
            if h and d and a:
                return [
                    BookmakerOddsDTO(bookmaker="pinnacle", market="1x2", outcome=o, price=p, ts=ts)
                    for o, p in (("home", h), ("draw", d), ("away", a))
                ]
        return []

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
