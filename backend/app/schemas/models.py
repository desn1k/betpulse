"""Admin ML model-registry schemas (Phase 12b)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.model_registry import ModelStatus
from app.models.model_weighting import WeightingMode


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    method: str
    version: str
    status: ModelStatus
    accuracy_pct: float | None
    brier: float | None
    log_loss: float | None
    roi_vs_closing: float | None
    sample_count: int
    is_enabled: bool
    is_visible: bool
    display_weight: float
    min_samples: int
    notes: str | None
    last_trained_at: datetime | None
    last_evaluated_at: datetime | None


class ModelsOut(BaseModel):
    models: list[ModelOut]
    weighting_mode: WeightingMode


class ModelUpdate(BaseModel):
    is_enabled: bool | None = None
    is_visible: bool | None = None
    notes: str | None = Field(default=None, max_length=512)


class WeightingModeIn(BaseModel):
    mode: WeightingMode


class WeightsIn(BaseModel):
    weights: dict[str, float]


class PromoteOut(BaseModel):
    promoted: bool
    warning: str | None = None


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reason: str
    actor: str | None
    taken_at: datetime


class RollbackDiffOut(BaseModel):
    changes: list[dict[str, object]]
