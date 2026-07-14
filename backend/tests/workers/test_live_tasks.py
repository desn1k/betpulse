"""ARQ live tasks: poll enqueues recompute + self-reschedules; swing enqueues push."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.core.redis import get_redis
from app.models.fixture import Fixture, FixtureStatus
from app.models.reference import League, ProviderLeagueAlias, ProviderTeamAlias, Team
from app.providers.api_football import ApiFootballProvider
from app.providers.dtos import LiveFixtureDTO, QuotaDTO
from app.workers import tasks as live_tasks
from sqlalchemy.ext.asyncio import AsyncSession

FIXTURES = Path(__file__).parent.parent / "fixtures" / "api_football"


class _FakeArq:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def enqueue_job(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((name, args, kwargs))

    def names(self) -> list[str]:
        return [j[0] for j in self.jobs]


class _FakeLiveProvider:
    name = "api_football"

    def __init__(self, dtos: list[LiveFixtureDTO]) -> None:
        self._dtos = dtos

    async def rate_limit_state(self) -> QuotaDTO:
        return QuotaDTO(provider=self.name, requests_remaining=100)

    async def fetch_live(self) -> list[LiveFixtureDTO]:
        return self._dtos


def _live_dtos() -> list[LiveFixtureDTO]:
    payload: dict[str, Any] = json.loads((FIXTURES / "live.json").read_text())
    return ApiFootballProvider.parse_live(payload)


async def _seed_epl(session: AsyncSession) -> None:
    league = League(code="EPL", name="Premier League", country="England")
    arsenal = Team(name="Arsenal", normalized_name="arsenal")
    chelsea = Team(name="Chelsea", normalized_name="chelsea")
    session.add_all([league, arsenal, chelsea])
    await session.flush()
    session.add_all(
        [
            ProviderLeagueAlias(provider="api_football", alias="39", league_id=league.id),
            ProviderTeamAlias(provider="api_football", alias="Arsenal", team_id=arsenal.id),
            ProviderTeamAlias(provider="api_football", alias="Chelsea", team_id=chelsea.id),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_poll_live_task_enqueues_recompute_and_reschedules(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_epl(session)
    monkeypatch.setattr(
        live_tasks, "build_live_provider", lambda _settings: _FakeLiveProvider(_live_dtos())
    )
    arq = _FakeArq()

    ingested = await live_tasks.poll_live_task({"redis": arq})

    assert ingested == 1  # one mapped fixture (the other is unmapped and skipped)
    names = arq.names()
    assert names.count("recompute_fixture_task") == 1
    assert "poll_live_task" in names  # self-reschedule
    # the reschedule carries a defer
    reschedule = next(j for j in arq.jobs if j[0] == "poll_live_task")
    assert "_defer_by" in reschedule[2]


@pytest.mark.asyncio
async def test_poll_live_task_skips_when_lock_held(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_epl(session)
    monkeypatch.setattr(
        live_tasks, "build_live_provider", lambda _settings: _FakeLiveProvider(_live_dtos())
    )
    await get_redis().set(live_tasks.LIVE_POLL_LOCK_KEY, "someone-else")

    ingested = await live_tasks.poll_live_task({"redis": _FakeArq()})
    assert ingested == 0


async def _seed_live_fixture(session: AsyncSession) -> uuid.UUID:
    league = League(code="EPL", name="Premier League")
    home = Team(name="Arsenal", normalized_name="arsenal")
    away = Team(name="Chelsea", normalized_name="chelsea")
    session.add_all([league, home, away])
    await session.flush()
    fixture = Fixture(
        league_id=league.id,
        season="2025",
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=datetime.now(tz=UTC),
        status=FixtureStatus.live,
    )
    session.add(fixture)
    await session.commit()
    return fixture.id


@pytest.mark.asyncio
async def test_recompute_task_enqueues_push_on_swing(session: AsyncSession) -> None:
    fid = await _seed_live_fixture(session)
    arq = _FakeArq()

    # First recompute establishes a baseline (no push).
    await live_tasks.recompute_fixture_task({"redis": arq}, str(fid), 10, 0, 0)
    assert "push_task" not in arq.names()

    # A late two-goal lead is a large swing → push enqueued.
    await live_tasks.recompute_fixture_task({"redis": arq}, str(fid), 85, 2, 0)
    assert "push_task" in arq.names()
