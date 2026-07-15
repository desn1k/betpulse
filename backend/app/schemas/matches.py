"""Response schemas for the public match read endpoints (Phase 6).

These power the frontend match list and match card. They expose only what the
public card needs — consensus, per-method 1X2 bars for *visible* methods, the
champion label, model-agreement and delta-vs-market — never internal fields.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.fixture import FixtureStatus

# The tier a guest/free user must reach before the per-method bars stop being
# blurred. Enforcement lands in Phase 7; the flag lets the frontend render the
# lock/blur placeholder already (the data is still returned for now).
METHODS_TIER_REQUIRED = "pro"


class LeagueRef(BaseModel):
    code: str
    name: str


class Probs1x2(BaseModel):
    """1X2 probabilities. Each in [0, 1]; the three sum to ~1."""

    home: float
    draw: float
    away: float


class MethodPrediction(BaseModel):
    method: str
    is_champion: bool
    accuracy_pct: float | None
    probs: Probs1x2


class MatchSummary(BaseModel):
    """One card in the match list."""

    id: uuid.UUID
    league: LeagueRef
    home_team: str
    away_team: str
    kickoff_at: datetime
    status: FixtureStatus
    minute: int | None
    home_score: int | None
    away_score: int | None
    consensus: Probs1x2 | None
    champion_method: str | None
    champion_accuracy_pct: float | None
    last_polled_at: datetime | None
    # True when last_polled_at is older than the freshness window (provider quota
    # exhaustion / stalled polling). Null last_polled_at (never polled) is not delayed.
    data_delayed: bool


class MatchList(BaseModel):
    items: list[MatchSummary]
    total: int
    limit: int
    offset: int


class MatchDetail(MatchSummary):
    """Full match card: every visible method bar plus the consensus context."""

    methods: list[MethodPrediction]
    market: Probs1x2 | None
    # Spread of the methods' home-win probabilities, normalized to 0–100
    # (100 = perfect agreement). Null when fewer than two methods are available.
    model_agreement_pct: float | None
    # consensus.home − market.home; null when no market/odds prediction exists.
    delta_vs_market: float | None
    # Tier required to see the method bars un-blurred (Phase 7 enforces it).
    tier_required: str = METHODS_TIER_REQUIRED
