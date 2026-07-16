"""LLM analysis: admin config (singleton) + per-fixture analyses (spec §8).

The LLM narrative *explains* the model outputs — it is never the source of the
probabilities. The provider is any OpenAI-compatible endpoint, configured by an
admin: ``base_url``, ``model`` and an API key encrypted at rest (Fernet, same as
provider keys); only a masked suffix is ever returned to a client. Generated
analyses are cached per ``(fixture_id, model)`` and their token usage + cost are
logged for the admin spend dashboard.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# Singleton row key so there is exactly one config row (see llm_config service).
LLM_CONFIG_SINGLETON = "default"


class LlmConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "llm_config"
    __table_args__ = (UniqueConstraint("singleton", name="uq_llm_config_singleton"),)

    # Always ``LLM_CONFIG_SINGLETON`` — enforces a single config row.
    singleton: Mapped[str] = mapped_column(String(16), nullable=False, default=LLM_CONFIG_SINGLETON)

    base_url: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # API key encrypted at rest (app.core.crypto); only key_suffix is returned.
    encrypted_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    key_suffix: Mapped[str | None] = mapped_column(String(8), nullable=True)

    max_tokens: Mapped[int] = mapped_column(Integer, default=600, nullable=False)
    daily_token_budget: Mapped[int] = mapped_column(Integer, default=100_000, nullable=False)
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, default=86_400, nullable=False)
    cost_per_1k_in: Mapped[Decimal] = mapped_column(Numeric(10, 5), default=0, nullable=False)
    cost_per_1k_out: Mapped[Decimal] = mapped_column(Numeric(10, 5), default=0, nullable=False)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class LlmAnalysis(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "llm_analyses"
    __table_args__ = (
        UniqueConstraint("fixture_id", "model", name="uq_llm_analysis_fixture_model"),
    )

    fixture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
