"""Historical bootstrap + verification orchestration."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixture import Fixture
from app.models.ingestion_run import IngestionRun, IngestionStatus
from app.models.market import Odds
from app.models.reference import League
from app.providers.base import ProviderQuotaExhausted
from app.providers.football_data_couk import (
    FootballDataCoUkProvider,
    canonical_to_fd_code,
    season_to_fd,
)
from app.services.ingestion.football_data import LEAGUE_META, IngestSummary, ingest_dtos

logger = logging.getLogger("ingestion.runner")

CsvSource = Callable[[str, str], Awaitable[bytes]]


def network_csv_source(provider: FootballDataCoUkProvider) -> CsvSource:
    """CSV source that downloads over the network, checking quota first."""

    async def _source(league_code: str, season: str) -> bytes:
        quota = await provider.rate_limit_state()
        if quota.requests_remaining <= 0:
            raise ProviderQuotaExhausted(
                f"provider '{provider.name}' has no remaining quota; refusing to fetch"
            )
        return await provider.download_csv(league_code, season)

    return _source


def offline_csv_source(directory: Path) -> CsvSource:
    """CSV source that reads committed fixtures ``{fd_code}_{fd_season}.csv``."""

    async def _source(league_code: str, season: str) -> bytes:
        path = directory / f"{canonical_to_fd_code(league_code)}_{season_to_fd(season)}.csv"
        return path.read_bytes()

    return _source


async def bootstrap_history(
    session: AsyncSession,
    *,
    leagues: list[str],
    seasons: list[str],
    csv_source: CsvSource,
    provider: FootballDataCoUkProvider | None = None,
) -> IngestSummary:
    provider = provider or FootballDataCoUkProvider()
    summary = IngestSummary()

    for league_code in leagues:
        if league_code == "RPL":
            logger.warning(
                json.dumps(
                    {
                        "event": "league_unsupported_by_source",
                        "league": "RPL",
                        "source": provider.name,
                        "detail": "no historical coverage; RPL uses the live provider's "
                        "history and is flagged beta in the UI",
                    }
                )
            )
            continue
        if league_code not in LEAGUE_META:
            logger.warning(json.dumps({"event": "league_unknown", "league": league_code}))
            continue

        for season in seasons:
            content = await csv_source(league_code, season)
            dtos = provider.parse_csv(content, league_code, season)
            summary.merge(await ingest_dtos(session, dtos, league_code=league_code))

    return summary


async def run_recorded_ingestion(
    session: AsyncSession,
    *,
    leagues: list[str],
    seasons: list[str],
    csv_source: CsvSource,
    provider_name: str,
    triggered_by: str | None = None,
) -> list[IngestionRun]:
    """Ingest each (league, season) pair, recording one ``ingestion_runs`` row per
    pair (provider, league, season, status, counts, duration, error). A failure on
    one pair is isolated in a savepoint and logged on its row; others still run.
    """
    runs: list[IngestionRun] = []
    for league_code in leagues:
        for season in seasons:
            run = IngestionRun(
                provider=provider_name,
                league=league_code,
                season=season,
                status=IngestionStatus.running,
                triggered_by=triggered_by,
            )
            session.add(run)
            await session.flush()

            started = time.monotonic()
            try:
                async with session.begin_nested():
                    summary = await bootstrap_history(
                        session,
                        leagues=[league_code],
                        seasons=[season],
                        csv_source=csv_source,
                    )
                run.fixtures_ingested = summary.fixtures_inserted
                run.odds_ingested = summary.odds_inserted
                run.status = (
                    IngestionStatus.success
                    if summary.fixtures_seen > 0
                    else IngestionStatus.partial
                )
            except Exception as exc:  # noqa: BLE001  (record any failure on the run)
                run.status = IngestionStatus.failed
                run.error = str(exc)[:2000]
                logger.warning(
                    json.dumps(
                        {
                            "event": "ingestion_run_failed",
                            "league": league_code,
                            "season": season,
                            "error": str(exc),
                        }
                    )
                )
            run.finished_at = datetime.now(UTC)
            run.duration_ms = int((time.monotonic() - started) * 1000)
            await session.flush()
            runs.append(run)
    return runs


@dataclass(slots=True)
class VerifyRow:
    league: str
    season: str
    fixture_count: int
    odds_count: int


async def verify_history(
    session: AsyncSession, *, leagues: list[str], seasons: list[str]
) -> tuple[list[VerifyRow], bool]:
    """Return per (league, season) counts and an overall ``ok`` flag.

    ``ok`` is False if any configured (league, season) has zero fixtures.
    """
    rows: list[VerifyRow] = []
    ok = True
    for league_code in leagues:
        if league_code not in LEAGUE_META:
            continue
        league_id = await session.scalar(select(League.id).where(League.code == league_code))
        for season in seasons:
            if league_id is None:
                rows.append(VerifyRow(league_code, season, 0, 0))
                ok = False
                continue
            fixture_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(Fixture)
                    .where(Fixture.league_id == league_id, Fixture.season == season)
                )
            ) or 0
            odds_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(Odds)
                    .join(Fixture, Odds.fixture_id == Fixture.id)
                    .where(Fixture.league_id == league_id, Fixture.season == season)
                )
            ) or 0
            rows.append(VerifyRow(league_code, season, fixture_count, odds_count))
            if fixture_count == 0:
                ok = False
    return rows, ok
