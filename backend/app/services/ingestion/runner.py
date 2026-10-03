"""Historical bootstrap + verification orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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
from app.services.ingestion.core import ensure_licensed
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
    # Refuse before downloading anything (dev / local source only).
    ensure_licensed(provider)
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


async def mark_stale_runs(
    session: AsyncSession, *, provider_name: str, now: datetime | None = None
) -> int:
    """Mark ``running`` rows older than ``ingestion_stale_run_minutes`` as
    failed: their worker died (a run never outlives the batch job timeout).
    A younger ``running`` row may belong to a live worker and is left alone.
    Returns how many were marked."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=get_settings().ingestion_stale_run_minutes)
    result = await session.execute(
        update(IngestionRun)
        .where(
            IngestionRun.provider == provider_name,
            IngestionRun.status == IngestionStatus.running,
            IngestionRun.started_at < cutoff,
        )
        .values(
            status=IngestionStatus.failed,
            error="stale: still running after the stale-run threshold (worker died)",
            finished_at=now,
        )
        .returning(IngestionRun.id)
    )
    return len(result.all())


async def _last_success_sha(
    session: AsyncSession, *, provider_name: str, league: str, season: str, exclude: object
) -> str | None:
    sha: str | None = await session.scalar(
        select(IngestionRun.content_sha256)
        .where(
            IngestionRun.provider == provider_name,
            IngestionRun.league == league,
            IngestionRun.season == season,
            IngestionRun.status == IngestionStatus.success,
            IngestionRun.content_sha256.is_not(None),
            IngestionRun.id != exclude,
        )
        .order_by(IngestionRun.started_at.desc())
        .limit(1)
    )
    return sha


async def run_recorded_ingestion(
    session: AsyncSession,
    *,
    leagues: list[str],
    seasons: list[str],
    csv_source: CsvSource,
    provider_name: str,
    triggered_by: str | None = None,
    force: bool = False,
    provider: FootballDataCoUkProvider | None = None,
) -> list[IngestionRun]:
    """Ingest each (league, season) pair, **committing after each pair** and
    recording one ``ingestion_runs`` row per pair (status, counts, duration,
    error, payload sha256). A failure on one pair is rolled back and logged on
    its row; other pairs still run, and committed pairs survive a crash.

    Resumable: a pair whose payload hashes the same as its last successful run
    is skipped (``skipped_reason = "unchanged"``) unless ``force``. Stale
    ``running`` rows from a dead worker are marked failed first.
    """
    provider = provider or FootballDataCoUkProvider()
    ensure_licensed(provider)
    await mark_stale_runs(session, provider_name=provider_name)
    await session.commit()

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
            await session.commit()  # visible as running while it works
            run_id = run.id

            started = time.monotonic()
            try:
                await _ingest_pair(
                    session,
                    run,
                    league_code=league_code,
                    season=season,
                    csv_source=csv_source,
                    provider=provider,
                    provider_name=provider_name,
                    force=force,
                )
            except Exception as exc:  # noqa: BLE001  (record any failure on the run)
                await session.rollback()
                failed = await session.get(IngestionRun, run_id)
                if failed is None:  # pragma: no cover - the row was committed above
                    raise
                run = failed
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
            await session.commit()
            runs.append(run)
    return runs


async def _ingest_pair(
    session: AsyncSession,
    run: IngestionRun,
    *,
    league_code: str,
    season: str,
    csv_source: CsvSource,
    provider: FootballDataCoUkProvider,
    provider_name: str,
    force: bool,
) -> None:
    if league_code not in LEAGUE_META:
        run.status = IngestionStatus.partial
        run.skipped_reason = "unsupported_league"
        logger.warning(json.dumps({"event": "league_unsupported_by_source", "league": league_code}))
        return
    content = await csv_source(league_code, season)
    run.content_sha256 = hashlib.sha256(content).hexdigest()
    previous = await _last_success_sha(
        session, provider_name=provider_name, league=league_code, season=season, exclude=run.id
    )
    if not force and previous == run.content_sha256:
        run.status = IngestionStatus.success
        run.skipped_reason = "unchanged"
        run.records = 0
        return
    dtos = provider.parse_csv(content, league_code, season)
    summary = await ingest_dtos(session, dtos, league_code=league_code)
    run.records = len(dtos)
    run.fixtures_ingested = summary.fixtures_inserted
    run.odds_ingested = summary.odds_inserted
    run.status = IngestionStatus.success if summary.fixtures_seen > 0 else IngestionStatus.partial


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
