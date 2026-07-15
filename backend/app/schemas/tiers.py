"""Admin tier schemas (spec §7: tiers are admin-editable data)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price: Decimal
    period: str | None
    feature_flags: dict[str, Any]
    limits: dict[str, Any]
    is_public: bool
    sort_order: int


class TierUpdate(BaseModel):
    """Partial edit of a tier. Only the provided fields are changed."""

    price: Decimal | None = Field(default=None, ge=0)
    period: str | None = None
    feature_flags: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    is_public: bool | None = None
