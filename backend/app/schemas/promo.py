"""Promo request/response schemas (spec §7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.promo import PromoBatchStatus, PromoCodeType, PromoRedemptionStatus

# --- admin: generation ------------------------------------------------------


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code_type: PromoCodeType
    # Multiple of 500 (validated server-side for a clear error message).
    size: int = Field(ge=500)
    value: Decimal | None = Field(default=None, ge=0)
    tier_id: uuid.UUID | None = None
    bound_user_id: uuid.UUID | None = None
    max_activations: int = Field(default=1, ge=1)
    expires_at: datetime | None = None
    stackable: bool = False


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code_type: PromoCodeType
    value: Decimal | None
    tier_id: uuid.UUID | None
    bound_user_id: uuid.UUID | None
    max_activations: int
    size: int
    stackable: bool
    expires_at: datetime | None
    status: PromoBatchStatus
    created_at: datetime


class BatchCreateOut(BaseModel):
    batch: BatchOut
    # Plaintext codes are returned ONCE — never stored, never retrievable again.
    codes: list[str]
    warning: str = "plaintext_codes_shown_once"


class KillOut(BaseModel):
    batch_id: uuid.UUID
    disabled_codes: int


# --- user: redemption -------------------------------------------------------


class RedeemRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class RedeemEffect(BaseModel):
    type: PromoCodeType
    value: Decimal | None
    status: PromoRedemptionStatus


class RedeemOut(BaseModel):
    effect: RedeemEffect
