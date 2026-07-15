"""User account model."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(enum.StrEnum):
    """Authenticated roles. ``guest`` is the absence of authentication and is
    therefore never persisted."""

    user = "user"
    admin = "admin"


class UserTier(enum.StrEnum):
    """Subscription tier. Gates access to premium features such as the live SSE
    stream (guest = unauthenticated, and ``free`` cannot stream). Phase 7 wires
    billing to move users between tiers; Phase 5 only needs the seam."""

    free = "free"
    pro = "pro"
    expert = "expert"

    @property
    def can_stream_live(self) -> bool:
        return self in (UserTier.pro, UserTier.expert)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.user, nullable=False
    )
    tier: Mapped[UserTier] = mapped_column(
        Enum(UserTier, name="user_tier"), default=UserTier.free, nullable=False
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # TOTP secret is stored encrypted at rest (see app.core.crypto).
    totp_secret_encrypted: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Per-account lockout state (exponential backoff, never a permanent lock).
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
