"""LLM analysis + admin config schemas (spec §8).

The public analysis payload always carries ``not_a_probability_source`` as a
top-level field so the frontend renders the disclaimer regardless of the model
text. Token counts and cost are deliberately **not** exposed publicly — they are
admin-only spend telemetry. The admin config never returns the raw key, only a
masked suffix.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["ru", "en"]


class AnalysisOut(BaseModel):
    """Public per-match LLM narrative. Never a source of probabilities."""

    status: Literal["ok", "budget_exhausted", "disabled", "no_data"]
    content: str | None = None
    model: str | None = None
    language: str = "en"
    cached: bool = False
    # Always true: the narrative explains the model outputs, it is not the source
    # of the numbers. Top-level so the client shows the disclaimer unconditionally.
    not_a_probability_source: bool = True
    # UTC-midnight ISO timestamp when the daily budget resets (budget_exhausted).
    resets_at: str | None = None
    is_match_of_the_day: bool = False


class LlmConfigOut(BaseModel):
    """Admin view of the singleton config. ``key_masked`` shows the last 4 chars
    only; the full key is never serialized."""

    model_config = ConfigDict(from_attributes=True)

    base_url: str
    model: str
    key_masked: str | None = None
    max_tokens: int
    daily_token_budget: int
    cache_ttl_seconds: int
    cost_per_1k_in: Decimal
    cost_per_1k_out: Decimal
    is_enabled: bool


class LlmConfigUpdate(BaseModel):
    """Partial edit of the singleton config. ``api_key`` is write-only: it is
    encrypted at rest and only its masked suffix is ever returned."""

    base_url: str | None = Field(default=None, max_length=256)
    model: str | None = Field(default=None, max_length=128)
    api_key: str | None = Field(default=None, min_length=1, max_length=256)
    max_tokens: int | None = Field(default=None, ge=1, le=4000)
    daily_token_budget: int | None = Field(default=None, ge=0)
    cache_ttl_seconds: int | None = Field(default=None, ge=0)
    cost_per_1k_in: Decimal | None = Field(default=None, ge=0)
    cost_per_1k_out: Decimal | None = Field(default=None, ge=0)
    is_enabled: bool | None = None
