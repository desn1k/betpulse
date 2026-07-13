"""Provider data-transfer objects — the cross-provider contract.

Every provider returns these exact shapes regardless of its upstream API, so the
ingestion layer and the ID-mapping resolver treat all sources uniformly.
Provider DTOs carry the provider's *raw* team/league names; the ID-mapping layer
resolves them to canonical internal ids.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class TeamRef(BaseModel):
    """A team as named by the provider (resolved to a canonical id downstream)."""

    model_config = ConfigDict(frozen=True)

    raw_name: str
    country: str | None = None


class LeagueRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    raw_name: str
    # The provider's own league code (e.g. football-data.co.uk "E0"), if any.
    raw_code: str | None = None
    country: str | None = None


class BookmakerOddsDTO(BaseModel):
    bookmaker: str
    market: str  # e.g. "1x2", "ou_2.5"
    outcome: str  # e.g. "home"/"draw"/"away"/"over"/"under"
    price: Decimal
    ts: datetime
    is_closing: bool = True


class StatsDTO(BaseModel):
    home_shots: int | None = None
    away_shots: int | None = None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None


class FixtureDTO(BaseModel):
    """A scheduled or finished fixture, with optional closing odds and stats."""

    provider: str
    league: LeagueRef
    season: str
    home: TeamRef
    away: TeamRef
    kickoff_at: datetime
    status: str = "finished"
    ft_home: int | None = None
    ft_away: int | None = None
    ht_home: int | None = None
    ht_away: int | None = None
    odds: list[BookmakerOddsDTO] = []
    stats: StatsDTO | None = None
    # Original CSV/row index for actionable ingestion warnings.
    source_row: int | None = None


class LiveFixtureDTO(BaseModel):
    provider: str
    league: LeagueRef
    home: TeamRef
    away: TeamRef
    minute: int
    home_score: int
    away_score: int
    status: str = "live"


class OddsDTO(BaseModel):
    provider: str
    fixture_ref: str
    prices: list[BookmakerOddsDTO] = []


class QuotaDTO(BaseModel):
    """Provider quota snapshot. Ingestion hard-stops when remaining hits 0."""

    provider: str
    requests_remaining: int
    resets_at: datetime | None = None
    daily_limit: int | None = None
