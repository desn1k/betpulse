"""Model governance tables (spec §16).

``model_registry`` holds one row per (method, version) with its rolling
out-of-sample metrics, consensus weight, and champion/challenger status.
``model_registry_snapshots`` stores the **full** registry state before any
governance change, so a promotion or weight change is one-click reversible.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ModelStatus(enum.StrEnum):
    challenger = "challenger"
    champion = "champion"
    retired = "retired"


class ModelRegistry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_registry"
    __table_args__ = (UniqueConstraint("method", "version", name="uq_registry_method_version"),)

    method: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Rolling out-of-sample metrics (null until the first evaluation runs).
    accuracy_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    brier: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    log_loss: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    roi_vs_closing: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    status: Mapped[ModelStatus] = mapped_column(
        Enum(ModelStatus, name="model_status"), default=ModelStatus.challenger, nullable=False
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_weight: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0, nullable=False)
    min_samples: Mapped[int] = mapped_column(Integer, default=300, nullable=False)

    last_trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)


class ModelRegistrySnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_registry_snapshots"

    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Full registry state at snapshot time: list of row dicts.
    payload: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
