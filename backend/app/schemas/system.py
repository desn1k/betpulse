"""Admin system health, audit viewer and ops-alert schemas (Phase 12d)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

HealthStatus = Literal["ok", "degraded", "error", "not_configured"]


class ComponentHealth(BaseModel):
    name: str
    status: HealthStatus
    detail: str | None = None
    latency_ms: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class SystemHealthOut(BaseModel):
    status: Literal["ok", "degraded", "error"]
    checked_at: datetime
    components: list[ComponentHealth]


class AuditLogRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    action: str
    target: str | None
    ip: str | None
    user_agent: str | None
    meta: dict[str, Any]
    created_at: datetime


class AuditLogList(BaseModel):
    events: list[AuditLogRow]
    total: int
    page: int
    per_page: int


class OpsAlertRequest(BaseModel):
    message: str = Field(default="BetPulse admin test alert", min_length=1, max_length=500)


class OpsAlertOut(BaseModel):
    status: Literal["sent", "not_configured"]
    detail: str | None = None
