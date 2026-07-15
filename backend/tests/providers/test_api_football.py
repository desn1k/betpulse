"""Contract test: ApiFootballProvider against recorded JSON (no live call)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from app.providers.api_football import ApiFootballProvider

FIXTURES = Path(__file__).parent.parent / "fixtures" / "api_football"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


def test_parse_live_maps_to_dtos() -> None:
    provider = ApiFootballProvider()
    live = provider.parse_live(_load("live.json"))
    assert len(live) == 2
    first = live[0]
    assert first.provider == "api_football"
    assert first.provider_fixture_id == "1035037"
    assert first.home.raw_name == "Arsenal"
    assert first.away.raw_name == "Chelsea"
    assert first.minute == 67
    assert (first.home_score, first.away_score) == (2, 1)
    assert first.league.raw_name == "Premier League"
    assert first.league.raw_code == "39"
    assert first.season == "2025"
    assert first.kickoff_at.year == 2026


def test_parse_quota_computes_remaining() -> None:
    provider = ApiFootballProvider()
    quota = provider.parse_quota(_load("status.json"))
    assert quota.daily_limit == 7500
    assert quota.requests_remaining == 7000


@pytest.mark.asyncio
@respx.mock
async def test_fetch_live_hits_endpoint_and_parses() -> None:
    respx.get("https://v3.football.api-sports.io/fixtures").mock(
        return_value=httpx.Response(200, json=_load("live.json"))
    )
    provider = ApiFootballProvider(api_key="test-key")
    live = await provider.fetch_live()
    assert len(live) == 2
    assert live[1].league.raw_name == "La Liga"


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_state_hits_status() -> None:
    respx.get("https://v3.football.api-sports.io/status").mock(
        return_value=httpx.Response(200, json=_load("status.json"))
    )
    provider = ApiFootballProvider(api_key="test-key")
    quota = await provider.rate_limit_state()
    assert quota.requests_remaining == 7000
