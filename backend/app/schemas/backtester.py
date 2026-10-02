"""Backtester request/response schemas (spec §6).

``StrategyFilter`` is a **whitelist**: only these typed fields ever reach the
query builder, and each becomes a bound parameter — user input is never
interpolated into SQL.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BetType(enum.StrEnum):
    x12 = "1x2"
    total = "total"  # over/under 2.5 goals


_VALID_PICKS = {
    BetType.x12: {"home", "draw", "away"},
    BetType.total: {"over", "under"},
}


# Decimal odds are always > 1.0 (1.0 would return only the stake). Anything
# above this bound is not a market price but a typo or a probe.
ODDS_UPPER_BOUND = 1000.0

# (min field, max field) pairs that must form an ordered range.
_RANGES = (
    ("odds_min", "odds_max"),
    ("elo_diff_min", "elo_diff_max"),
    ("avg_total_min", "avg_total_max"),
)


class StrategyFilter(BaseModel):
    # Reject unknown filter fields, and NaN/inf in every numeric filter.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    league: str | None = Field(default=None, max_length=32)
    season: str | None = Field(default=None, max_length=16)
    # Range on the odds of the *picked* selection.
    odds_min: float | None = Field(default=None, gt=1.0, le=ODDS_UPPER_BOUND)
    odds_max: float | None = Field(default=None, gt=1.0, le=ODDS_UPPER_BOUND)
    elo_diff_min: float | None = None
    elo_diff_max: float | None = None
    avg_total_min: float | None = Field(default=None, ge=0)
    avg_total_max: float | None = Field(default=None, ge=0)
    rest_days_min: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_ranges(self) -> StrategyFilter:
        for low_name, high_name in _RANGES:
            low, high = getattr(self, low_name), getattr(self, high_name)
            if low is not None and high is not None and low > high:
                raise ValueError(f"{low_name} must be less than or equal to {high_name}")
        return self


class RunRequest(BaseModel):
    bet_type: BetType
    pick: str
    filters: StrategyFilter = Field(default_factory=StrategyFilter)

    @model_validator(mode="after")
    def _check_pick(self) -> RunRequest:
        if self.pick not in _VALID_PICKS[self.bet_type]:
            allowed = ", ".join(sorted(_VALID_PICKS[self.bet_type]))
            raise ValueError(f"pick must be one of: {allowed} for bet_type {self.bet_type.value}")
        return self


class WilsonInterval(BaseModel):
    lower: float
    upper: float
    confidence: float


class Breakdown(BaseModel):
    key: str
    matched_count: int
    roi: float


class SeasonSplit(BaseModel):
    season: str
    matched_count: int
    roi: float


class BacktestResult(BaseModel):
    bet_type: BetType
    pick: str
    matched_count: int
    win_count: int
    win_rate: float
    roi: float
    total_staked: float
    total_return: float
    equity_curve: list[float]
    max_drawdown: float
    win_rate_ci: WilsonInterval
    by_league: list[Breakdown]
    by_season: list[Breakdown]
    available_bet_types: list[BetType]
    # Forced on every client: the responsible-use warning next to any ROI figure.
    roi_disclaimer: bool = True
    small_sample_warning: bool
    # How these numbers were obtained: a fixed rule replayed over historical
    # matches, settled at the closing quote. Nothing is trained or re-fitted,
    # so no part of the result is an out-of-sample test.
    evaluation_protocol: str = "historical_rule_simulation"
    odds_basis: str = "closing"
    # Per-season breakdown in chronological order (only when requested). Plain
    # grouping of the same settled bets — not a walk-forward evaluation.
    season_split: bool = False
    season_splits: list[SeasonSplit] = Field(default_factory=list)


class StrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    bet_type: BetType
    pick: str
    filters: StrategyFilter = Field(default_factory=StrategyFilter)


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    bet_type: str
    pick: str
    filters: dict[str, Any]
    created_at: datetime
