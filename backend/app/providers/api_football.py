"""API-Football provider (roles: live, odds; optionally xg).

Phase 3 ships the interface plus the response parsers. Parsing is pure and
tested against a recorded JSON payload (no live call, no key in CI). Real
ingestion wiring lands in Phase 5.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.providers.base import (
    BaseProvider,
    Capability,
    DateRange,
    NotSupportedError,
)
from app.providers.dtos import (
    FixtureDTO,
    LeagueRef,
    LiveFixtureDTO,
    OddsDTO,
    QuotaDTO,
    StatsDTO,
    TeamRef,
)

DEFAULT_BASE_URL = "https://v3.football.api-sports.io"


class ApiFootballProvider(BaseProvider):
    name = "api_football"
    capabilities = frozenset({Capability.LIVE, Capability.ODDS})

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        daily_limit: int = 7500,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._daily_limit = daily_limit
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"x-apisports-key": self._api_key},
            timeout=self._timeout,
        )

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._client() as client:
            resp = await client.get(path, params=params or {})
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

    # --- parsers (pure, unit-testable) -------------------------------------

    @staticmethod
    def parse_live(payload: dict[str, Any]) -> list[LiveFixtureDTO]:
        fixtures: list[LiveFixtureDTO] = []
        for item in payload.get("response", []):
            league = item.get("league", {})
            teams = item.get("teams", {})
            goals = item.get("goals", {})
            status = item.get("fixture", {}).get("status", {})
            fixtures.append(
                LiveFixtureDTO(
                    provider="api_football",
                    league=LeagueRef(
                        raw_name=league.get("name", ""),
                        raw_code=str(league.get("id")) if league.get("id") else None,
                        country=league.get("country"),
                    ),
                    home=TeamRef(raw_name=teams.get("home", {}).get("name", "")),
                    away=TeamRef(raw_name=teams.get("away", {}).get("name", "")),
                    minute=int(status.get("elapsed") or 0),
                    home_score=int(goals.get("home") or 0),
                    away_score=int(goals.get("away") or 0),
                )
            )
        return fixtures

    def parse_quota(self, payload: dict[str, Any]) -> QuotaDTO:
        usage = payload.get("response", {}).get("requests", {})
        limit = int(usage.get("limit_day") or self._daily_limit)
        current = int(usage.get("current") or 0)
        return QuotaDTO(
            provider=self.name,
            requests_remaining=max(limit - current, 0),
            resets_at=None,
            daily_limit=limit,
        )

    # --- BaseProvider interface --------------------------------------------

    async def fetch_fixtures(self, date_range: DateRange) -> list[FixtureDTO]:
        raise NotSupportedError(
            "api_football historical fetch is wired in Phase 5 (live ingestion)"
        )

    async def fetch_live(self) -> list[LiveFixtureDTO]:
        return self.parse_live(await self._get("/fixtures", {"live": "all"}))

    async def fetch_odds(self, fixture_id: str) -> OddsDTO:
        # Full odds parsing lands in Phase 5; the request path is exercised here.
        await self._get("/odds", {"fixture": fixture_id})
        return OddsDTO(provider=self.name, fixture_ref=fixture_id, prices=[])

    async def fetch_stats(self, fixture_id: str) -> StatsDTO:
        await self._get("/fixtures/statistics", {"fixture": fixture_id})
        return StatsDTO()

    async def rate_limit_state(self) -> QuotaDTO:
        return self.parse_quota(await self._get("/status"))
