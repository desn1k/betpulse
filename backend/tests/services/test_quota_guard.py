"""Quota guard: hard-stop BEFORE fetching when nothing remains."""

from __future__ import annotations

from datetime import date

import pytest
from app.providers.base import BaseProvider, Capability, DateRange, ProviderQuotaExhausted
from app.providers.dtos import FixtureDTO, LiveFixtureDTO, OddsDTO, QuotaDTO, StatsDTO
from app.services.ingestion.quota import fetch_fixtures_guarded

_RANGE = DateRange(start=date(2023, 8, 1), end=date(2024, 5, 1))


class _FakeProvider(BaseProvider):
    name = "fake"
    capabilities = frozenset({Capability.HISTORICAL})

    def __init__(self, remaining: int) -> None:
        self._remaining = remaining
        self.fetch_called = False

    async def rate_limit_state(self) -> QuotaDTO:
        return QuotaDTO(provider=self.name, requests_remaining=self._remaining)

    async def fetch_fixtures(self, date_range: DateRange) -> list[FixtureDTO]:
        self.fetch_called = True
        return []

    async def fetch_live(self) -> list[LiveFixtureDTO]:
        return []

    async def fetch_odds(self, fixture_id: str) -> OddsDTO:
        return OddsDTO(provider=self.name, fixture_ref=fixture_id)

    async def fetch_stats(self, fixture_id: str) -> StatsDTO:
        return StatsDTO()


@pytest.mark.asyncio
async def test_zero_quota_hard_stops_without_fetching() -> None:
    provider = _FakeProvider(remaining=0)
    with pytest.raises(ProviderQuotaExhausted):
        await fetch_fixtures_guarded(provider, _RANGE)
    assert provider.fetch_called is False


@pytest.mark.asyncio
async def test_positive_quota_allows_fetch() -> None:
    provider = _FakeProvider(remaining=5)
    result = await fetch_fixtures_guarded(provider, _RANGE)
    assert result == []
    assert provider.fetch_called is True
