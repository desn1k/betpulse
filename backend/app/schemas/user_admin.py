"""Admin user-management schemas (spec §9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.promo import PromoCodeType, PromoRedemptionStatus
from app.models.user import UserRole, UserTier


class UserRow(BaseModel):
    """One row in the admin user list. ``effective_tier`` is the resolved tier
    (most-privileged active subscription, else the base ``users.tier``)."""

    id: uuid.UUID
    email: str
    role: UserRole
    base_tier: UserTier
    effective_tier: str
    tier_expires_at: datetime | None
    is_active: bool
    is_verified: bool
    created_at: datetime


class UserList(BaseModel):
    users: list[UserRow]
    total: int
    page: int
    per_page: int


class TierAssign(BaseModel):
    """Grant a tier manually. Creates a ``source=manual`` subscription; a null
    ``expires_at`` is a perpetual grant."""

    tier_id: uuid.UUID
    expires_at: datetime | None = None


class UserMutationOut(BaseModel):
    id: uuid.UUID
    is_active: bool
    effective_tier: str
    tier_expires_at: datetime | None


class RedemptionRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    batch_id: uuid.UUID
    code_type: PromoCodeType
    value: Decimal | None
    status: PromoRedemptionStatus
    redeemed_at: datetime


class DisableOut(BaseModel):
    id: uuid.UUID
    is_active: bool
    revoked_tokens: int = Field(description="Refresh tokens revoked in the same transaction")
