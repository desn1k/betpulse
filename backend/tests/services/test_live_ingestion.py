"""Live ingestion: strict resolution, unmapped skip, and idempotency."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.models.fixture import Fixture, FixtureStatus
from app.models.reference import League, ProviderLeagueAlias, ProviderTeamAlias, Team
from app.providers.api_football import ApiFootballProvider
from app.providers.dtos import LiveFixtureDTO
from app.services.live.ingestion import poll_live
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURES = Path(__file__).parent.parent / "fixtures" / "api_football"


def _load_live_dtos() -> list[LiveFixtureDTO]:
    payload: dict[str, Any] = json.loads((FIXTURES / "live.json").read_text())
    return ApiFootballProvider.parse_live(payload)


async def _seed_epl_aliases(session: AsyncSession) -> None:
    """Seed only the EPL fixture's canonical entities + API-Football aliases.

    The La Liga fixture in the cassette is intentionally left unmapped so the
    skip-with-warning path is exercised.
    """
    league = League(code="EPL", name="Premier League", country="England")
    arsenal = Team(name="Arsenal", normalized_name="arsenal", country="England")
    chelsea = Team(name="Chelsea", normalized_name="chelsea", country="England")
    session.add_all([league, arsenal, chelsea])
    await session.flush()
    session.add_all(
        [
            ProviderLeagueAlias(provider="api_football", alias="39", league_id=league.id),
            ProviderTeamAlias(provider="api_football", alias="Arsenal", team_id=arsenal.id),
            ProviderTeamAlias(provider="api_football", alias="Chelsea", team_id=chelsea.id),
        ]
    )
    await session.flush()


@pytest.mark.asyncio
async def test_poll_live_ingests_mapped_and_skips_unmapped(session: AsyncSession) -> None:
    await _seed_epl_aliases(session)
    provider = ApiFootballProvider()

    result = await poll_live(session, provider, dtos=_load_live_dtos())

    assert result.seen == 2
    assert result.ingested == 1
    assert result.skipped_unmapped == 1
    assert len(result.states) == 1
    state = result.states[0]
    assert (state.minute, state.home_score, state.away_score) == (67, 2, 1)

    fixture = (await session.execute(select(Fixture))).scalar_one()
    assert fixture.status == FixtureStatus.live
    assert fixture.minute == 67


@pytest.mark.asyncio
async def test_poll_live_is_idempotent(session: AsyncSession) -> None:
    await _seed_epl_aliases(session)
    provider = ApiFootballProvider()
    dtos = _load_live_dtos()

    await poll_live(session, provider, dtos=dtos)
    await poll_live(session, provider, dtos=dtos)

    count = (await session.execute(select(func.count()).select_from(Fixture))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_poll_live_advances_minute_in_place(session: AsyncSession) -> None:
    await _seed_epl_aliases(session)
    provider = ApiFootballProvider()
    dtos = _load_live_dtos()

    await poll_live(session, provider, dtos=dtos)
    # Same match, later minute + a new goal.
    dtos[0] = dtos[0].model_copy(update={"minute": 80, "home_score": 3})
    result = await poll_live(session, provider, dtos=dtos)

    assert result.states[0].minute == 80
    count = (await session.execute(select(func.count()).select_from(Fixture))).scalar_one()
    assert count == 1
    fixture = (await session.execute(select(Fixture))).scalar_one()
    assert fixture.minute == 80
