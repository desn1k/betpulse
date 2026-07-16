"""Recorded historical ingestion: one ingestion_runs row per league/season, with
success/failed status isolated per pair (Phase 12a)."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.models.ingestion_run import IngestionStatus
from app.services.ingestion.runner import offline_csv_source, run_recorded_ingestion
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "football_data"


@pytest.mark.asyncio
async def test_records_success_and_failure_per_pair(session: AsyncSession) -> None:
    # EPL 2023-2024 has a committed fixture (E0_2324.csv) → success; LALIGA has no
    # fixture file → the offline source raises → that pair is recorded as failed,
    # isolated in a savepoint so the successful pair still persists.
    runs = await run_recorded_ingestion(
        session,
        leagues=["EPL", "LALIGA"],
        seasons=["2023-2024"],
        csv_source=offline_csv_source(FIXTURE_DIR),
        provider_name="football_data_couk",
        triggered_by="cron",
    )
    await session.commit()

    by_league = {r.league: r for r in runs}
    assert by_league["EPL"].status == IngestionStatus.success
    assert by_league["EPL"].fixtures_ingested > 0
    assert by_league["EPL"].duration_ms is not None

    assert by_league["LALIGA"].status == IngestionStatus.failed
    assert by_league["LALIGA"].error is not None
    assert by_league["LALIGA"].fixtures_ingested == 0
