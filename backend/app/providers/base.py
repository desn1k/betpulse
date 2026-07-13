"""BaseProvider abstraction (spec §2).

BaseProvider (abstract)
  ├─ capabilities: {historical, live, odds, xg}
  ├─ fetch_fixtures(date_range) -> list[FixtureDTO]
  ├─ fetch_live() -> list[LiveFixtureDTO]
  ├─ fetch_odds(fixture_id) -> OddsDTO
  ├─ fetch_stats(fixture_id) -> StatsDTO
  └─ rate_limit_state() -> QuotaDTO
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from app.providers.dtos import (
    FixtureDTO,
    LiveFixtureDTO,
    OddsDTO,
    QuotaDTO,
    StatsDTO,
)


class Capability(enum.StrEnum):
    HISTORICAL = "historical"
    LIVE = "live"
    ODDS = "odds"
    XG = "xg"


@dataclass(frozen=True, slots=True)
class DateRange:
    start: date
    end: date


class ProviderError(Exception):
    """Base class for provider errors."""


class NotSupportedError(ProviderError):
    """Raised when a provider is asked for a capability it does not have."""


class ProviderQuotaExhausted(ProviderError):
    """Raised when a provider's request quota is exhausted (never overspend)."""


class BaseProvider(ABC):
    name: str
    capabilities: frozenset[Capability]

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @abstractmethod
    async def fetch_fixtures(self, date_range: DateRange) -> list[FixtureDTO]: ...

    @abstractmethod
    async def fetch_live(self) -> list[LiveFixtureDTO]: ...

    @abstractmethod
    async def fetch_odds(self, fixture_id: str) -> OddsDTO: ...

    @abstractmethod
    async def fetch_stats(self, fixture_id: str) -> StatsDTO: ...

    @abstractmethod
    async def rate_limit_state(self) -> QuotaDTO: ...
