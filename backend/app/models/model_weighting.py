"""Consensus weighting mode (Phase 12b).

A single admin-editable row selecting how consensus display weights are set:
``auto`` (softmax of per-method accuracy, recomputed on the nightly re-eval) or
``manual`` (an admin sets the weights, which the re-eval then leaves alone).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import UUIDPrimaryKeyMixin

# Singleton row key so there is exactly one weighting-mode row.
MODEL_WEIGHTING_SINGLETON = "default"


class WeightingMode(enum.StrEnum):
    auto = "auto"
    manual = "manual"


class ModelWeighting(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_weighting"
    __table_args__ = (UniqueConstraint("singleton", name="uq_model_weighting_singleton"),)

    singleton: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MODEL_WEIGHTING_SINGLETON
    )
    mode: Mapped[WeightingMode] = mapped_column(
        Enum(WeightingMode, name="weighting_mode"),
        default=WeightingMode.auto,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
