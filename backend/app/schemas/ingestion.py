"""Admin ingestion job-log + re-scan schemas (Phase 12a)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.ingestion_run import IngestionStatus


class IngestionRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    league: str | None
    season: str | None
    status: IngestionStatus
    fixtures_ingested: int
    odds_ingested: int
    error: str | None
    triggered_by: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None


class IngestionRunsOut(BaseModel):
    runs: list[IngestionRunOut]
    total: int
    page: int
    per_page: int


class RescanRequest(BaseModel):
    leagues: list[str] = Field(min_length=1)
    seasons: list[str] = Field(min_length=1)


class RescanAccepted(BaseModel):
    accepted: bool = True
    leagues: list[str]
    seasons: list[str]
