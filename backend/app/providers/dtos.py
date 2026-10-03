"""Provider data-transfer objects — the cross-provider contract.

Every provider returns these exact shapes regardless of its upstream API, so the
ingestion layer and the ID-mapping resolver treat all sources uniformly.
Provider DTOs carry the provider's *raw* team/league names; the ID-mapping layer
resolves them to canonical internal ids.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


def _aware_utc(value: datetime) -> datetime:
    """Sources must send time-zone-aware datetimes; stored as UTC. A naive
    value is rejected — guessing its zone is how kickoffs drift by an hour."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be time-zone aware (UTC)")
    return value.astimezone(UTC)


class TeamRef(BaseModel):
    """A team as named by the provider (resolved to a canonical id downstream)."""

    model_config = ConfigDict(frozen=True)

    raw_name: str
    country: str | None = None
    # The provider's stable team id, when it has one (resolved before the name).
    external_id: str | None = None


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

    _ts_utc = field_validator("ts")(_aware_utc)


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
    # The source's own id for this fixture (synthetic when it has none); the
    # first identity check on ingestion (``fixture_external_refs``).
    external_id: str | None = None
    league: LeagueRef
    season: str
    home: TeamRef
    away: TeamRef
    kickoff_at: datetime
    # False for date-only sources: kickoff_at is then 12:00 UTC of the date.
    kickoff_time_known: bool = True
    status: str = "finished"
    ft_home: int | None = None
    ft_away: int | None = None
    ht_home: int | None = None
    ht_away: int | None = None
    odds: list[BookmakerOddsDTO] = []
    stats: StatsDTO | None = None
    # Original CSV/row index for actionable ingestion warnings.
    source_row: int | None = None

    _kickoff_utc = field_validator("kickoff_at")(_aware_utc)


class LiveFixtureDTO(BaseModel):
    provider: str
    # The provider's own fixture id (e.g. API-Football numeric id), kept for
    # traceability in warnings; canonical identity is (league, season, pairing,
    # kickoff) via ``uq_fixture_identity``.
    provider_fixture_id: str
    league: LeagueRef
    season: str
    home: TeamRef
    away: TeamRef
    kickoff_at: datetime
    minute: int
    home_score: int
    away_score: int
    status: str = "live"

    _kickoff_utc = field_validator("kickoff_at")(_aware_utc)


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
