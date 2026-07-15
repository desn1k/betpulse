"""Subscription tiers and user subscriptions (spec §7).

Tiers are **data, not hardcode**: ``feature_flags`` and ``limits`` (both JSONB)
drive server-side authorization and the frontend's blur/lock UX, and an admin
edits them at runtime. ``app.services.tiers`` holds the code-defined defaults
that seed these rows and act as a fallback when a row is absent.

``guest`` is modelled as a tier row too (the unauthenticated baseline), so the
same resolution and flag machinery covers anonymous visitors.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class SubscriptionSource(enum.StrEnum):
    """How a subscription was granted. ``payment`` is reserved for the billing
    seam (``app.services.billing.PaymentProvider``) — no implementation yet."""

    manual = "manual"
    promo = "promo"
    payment = "payment"


class Tier(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tiers"

    # Canonical name: guest | free | pro | expert (extensible by the admin).
    name: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0, nullable=False)
    # Billing period label, e.g. "month" / "year". Free/guest have none.
    period: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Authorization surface (§7): both drive API gating and frontend UX.
    feature_flags: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "tier_id", name="uq_subscription_user_tier"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tiers.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    source: Mapped[SubscriptionSource] = mapped_column(
        Enum(SubscriptionSource, name="subscription_source"), nullable=False
    )
    # Null = perpetual (e.g. a manual grant). A past value means the subscription
    # has lapsed and no longer overrides the user's base tier.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
