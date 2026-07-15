"""Ingestion: idempotency, verify-history counts, and the CLI entrypoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.cli import _bootstrap, _verify
from app.models.fixture import Fixture
from app.models.market import Odds
from app.models.reference import Team
from app.providers.dtos import FixtureDTO
from app.providers.football_data_couk import FootballDataCoUkProvider
from app.services.ingestion.football_data import ingest_dtos
from app.services.ingestion.runner import verify_history
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "football_data"


def _dtos() -> list[FixtureDTO]:
    content = (FIXTURE_DIR / "E0_2324.csv").read_bytes()
    return FootballDataCoUkProvider().parse_csv(content, "EPL", "2023-2024")


async def _count(session: AsyncSession, model: Any) -> int:
    return (await session.scalar(select(func.count()).select_from(model))) or 0


@pytest.mark.asyncio
async def test_ingestion_is_idempotent(session: AsyncSession) -> None:
    dtos = _dtos()

    first = await ingest_dtos(session, dtos, league_code="EPL")
    assert first.fixtures_inserted == 10
    assert first.teams_created == 20  # matchday 1 → 20 distinct teams
    assert first.leagues_created == 1
    assert first.odds_inserted == 50  # 10 fixtures x (3 1X2 + 2 over/under)

    # Re-running the same CSV inserts nothing new.
    second = await ingest_dtos(session, dtos, league_code="EPL")
    assert second.fixtures_inserted == 0
    assert second.odds_inserted == 0
    assert second.teams_created == 0

    assert await _count(session, Fixture) == 10
    assert await _count(session, Odds) == 50
    assert await _count(session, Team) == 20


@pytest.mark.asyncio
async def test_verify_history_reports_counts(session: AsyncSession) -> None:
    await ingest_dtos(session, _dtos(), league_code="EPL")
    await session.flush()

    rows, ok = await verify_history(session, leagues=["EPL"], seasons=["2023-2024"])
    assert ok is True
    assert rows[0].fixture_count == 10
    assert rows[0].odds_count == 50

    # A configured league with no data fails the check.
    rows2, ok2 = await verify_history(session, leagues=["LALIGA"], seasons=["2023-2024"])
    assert ok2 is False
    assert rows2[0].fixture_count == 0


@pytest.mark.asyncio
async def test_cli_bootstrap_then_verify_offline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = await _bootstrap(["EPL"], ["2023-2024"], offline_dir=str(FIXTURE_DIR))
    assert rc == 0

    rc_ok = await _verify(["EPL"], ["2023-2024"])
    assert rc_ok == 0
    table = capsys.readouterr().out
    assert "fixtures" in table and "EPL" in table

    rc_fail = await _verify(["LALIGA"], ["2023-2024"])
    assert rc_fail == 1
