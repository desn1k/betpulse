"""Ingestion run log (Phase 12a).

One row per historical-ingestion run (a bootstrap / admin re-scan), recording
what was ingested and how it finished, so the admin dashboard can show a job
log with per-run status, counts, duration, and errors. Rows are written by the
recorded-ingestion wrapper; a row is created ``running`` and finalised in place.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import UUIDPrimaryKeyMixin


class IngestionStatus(enum.StrEnum):
    running = "running"
    success = "success"
    partial = "partial"
    failed = "failed"


class IngestionRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ingestion_runs"

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    league: Mapped[str | None] = mapped_column(String(32), nullable=True)
    season: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus, name="ingestion_status"),
        default=IngestionStatus.running,
        nullable=False,
        index=True,
    )
    fixtures_ingested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    odds_ingested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "cron" or "admin:{user_id}" — who kicked off this run.
    triggered_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
