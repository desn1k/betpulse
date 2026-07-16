"""Live-streaming and push-notification models (Phase 5).

``LiveUpdate`` is an append-only event log whose ``BIGSERIAL`` id is the SSE
``Last-Event-ID`` — a monotonic cursor the Timescale ``predictions_live``
hypertable cannot provide (its primary key is composite). ``PushSubscription``
stores a user's Telegram chat or Web Push endpoint so the push worker can reach
them; the Web Push keys are not secrets (they are the browser's public keys), so
they are stored as-is.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class PushChannel(enum.StrEnum):
    telegram = "telegram"
    webpush = "webpush"


class LiveUpdate(Base):
    """Append-only live-update event. ``id`` (BIGSERIAL) is the SSE cursor."""

    __tablename__ = "live_updates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fixture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), index=True, nullable=False
    )
    minute: Mapped[int] = mapped_column(Integer, nullable=False)
    home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, nullable=False)
    # Serialized SSE payload (probabilities per market/outcome + match state).
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )


class PushSubscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's push destination. One row per (user, channel, endpoint)."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "channel", "endpoint", name="uq_push_subscription"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel: Mapped[PushChannel] = mapped_column(
        Enum(PushChannel, name="push_channel"), nullable=False
    )
    # Telegram: the chat id. Web Push: the endpoint URL.
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    # Web Push only: {"p256dh": ..., "auth": ...} browser public keys. Empty for
    # Telegram. These are public per the Web Push spec — not encrypted secrets.
    keys: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class PushFollow(UUIDPrimaryKeyMixin, Base):
    """A user following a fixture: swing pushes for that fixture go only to its
    followers (Phase 11), rather than to every subscriber.

    A follow is created or deleted, never updated — so only ``created_at`` is
    stored (no ``updated_at``); the migration for ``push_follows`` matches this.
    """

    __tablename__ = "push_follows"
    __table_args__ = (UniqueConstraint("user_id", "fixture_id", name="uq_push_follow"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    fixture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TelegramLinkToken(UUIDPrimaryKeyMixin, Base):
    """A one-time token for connecting a Telegram chat via a deep link.

    Only the SHA-256 hash is stored; the plaintext lives only in the
    ``t.me/<bot>?start=<token>`` link shown to the user once. Single-use
    (``used_at``) with a short expiry (see the telegram-link service).
    """

    __tablename__ = "telegram_link_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_telegram_link_token_hash"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
