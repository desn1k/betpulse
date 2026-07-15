"""Promo batches, codes and redemptions (spec §7).

Codes are never stored in plaintext — only an HMAC ``code_hash`` (keyed by
``DATA_ENCRYPTION_KEY``) is persisted, and the plaintext is shown to the admin
exactly once at generation time. ``max_activations`` is denormalised onto
``promo_codes`` so a redemption can atomically claim a slot with a single guarded
``UPDATE`` (no read-then-write race). ``promo_redemptions`` records every
successful redemption; for ``trial``/``upgrade`` a subscription is created and the
row is ``applied``, for ``percent``/``fixed`` it stays ``pending`` until checkout
(the billing seam).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class PromoCodeType(enum.StrEnum):
    percent = "percent"  # value = discount %
    fixed = "fixed"  # value = fixed amount off
    trial = "trial"  # value = number of days of the target tier
    upgrade = "upgrade"  # grant the target tier (value unused)


class PromoBatchStatus(enum.StrEnum):
    active = "active"
    disabled = "disabled"  # killed by the admin kill-switch


class PromoCodeStatus(enum.StrEnum):
    active = "active"
    redeemed = "redeemed"  # all activations spent
    disabled = "disabled"  # killed with its batch


class PromoRedemptionStatus(enum.StrEnum):
    applied = "applied"  # trial/upgrade → subscription created
    pending = "pending"  # percent/fixed → waits for checkout
    expired = "expired"


class PromoBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "promo_batches"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code_type: Mapped[PromoCodeType] = mapped_column(
        Enum(PromoCodeType, name="promo_code_type"), nullable=False
    )
    # Discount %, fixed amount, or trial days depending on code_type. Null for upgrade.
    value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Target tier for trial/upgrade (and the tier a discount applies to).
    tier_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tiers.id", ondelete="RESTRICT"), nullable=True
    )
    # Optional: bind the whole batch to one user (only they may redeem).
    bound_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    max_activations: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Batches are generated in multiples of 500 (validated in the service).
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    # Discount stacking rule (applied at checkout, later phase). Default: take max.
    stackable: Mapped[bool] = mapped_column(default=False, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[PromoBatchStatus] = mapped_column(
        Enum(PromoBatchStatus, name="promo_batch_status"),
        default=PromoBatchStatus.active,
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PromoCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "promo_codes"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("promo_batches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # HMAC-SHA256 of the plaintext, keyed by DATA_ENCRYPTION_KEY. Unique; the
    # plaintext is never stored.
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    activations_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Denormalised from the batch so the redemption UPDATE is a single guarded row.
    max_activations: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[PromoCodeStatus] = mapped_column(
        Enum(PromoCodeStatus, name="promo_code_status"),
        default=PromoCodeStatus.active,
        nullable=False,
    )
    # Set to the redeemer on first use (records the claimant).
    bound_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PromoRedemption(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "promo_redemptions"
    __table_args__ = (
        # A user may redeem a given code once (max_activations governs the total).
        UniqueConstraint("user_id", "code_hash", name="uq_redemption_user_code"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("promo_batches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_type: Mapped[PromoCodeType] = mapped_column(
        Enum(PromoCodeType, name="promo_code_type"), nullable=False
    )
    value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    status: Mapped[PromoRedemptionStatus] = mapped_column(
        Enum(PromoRedemptionStatus, name="promo_redemption_status"), nullable=False
    )
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
