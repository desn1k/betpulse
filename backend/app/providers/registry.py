"""Provider registry.

Maps provider names to their classes and exposes the list of implemented
providers (used by the docs CI check that every provider has a
``docs/DATA_SOURCES.md`` section).
"""

from __future__ import annotations

from app.providers.api_football import ApiFootballProvider
from app.providers.base import BaseProvider
from app.providers.football_data_couk import FootballDataCoUkProvider

PROVIDERS: dict[str, type[BaseProvider]] = {
    FootballDataCoUkProvider.name: FootballDataCoUkProvider,
    ApiFootballProvider.name: ApiFootballProvider,
}


def implemented_provider_names() -> list[str]:
    return sorted(PROVIDERS)
