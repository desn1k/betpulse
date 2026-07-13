"""Quota guard: check a provider's remaining quota BEFORE spending a request.

Ingestion never overspends — it asks ``rate_limit_state()`` first and hard-stops
(raises :class:`ProviderQuotaExhausted`) when nothing remains, without issuing
the fetch.
"""

from __future__ import annotations

from app.providers.base import BaseProvider, DateRange, ProviderQuotaExhausted
from app.providers.dtos import FixtureDTO


async def fetch_fixtures_guarded(provider: BaseProvider, date_range: DateRange) -> list[FixtureDTO]:
    quota = await provider.rate_limit_state()
    if quota.requests_remaining <= 0:
        raise ProviderQuotaExhausted(
            f"provider '{provider.name}' has no remaining quota "
            f"(resets_at={quota.resets_at}); refusing to fetch"
        )
    return await provider.fetch_fixtures(date_range)
