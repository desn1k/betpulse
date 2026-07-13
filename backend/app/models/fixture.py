"""Fixtures and their per-match detail (stats, shots)."""

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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class FixtureStatus(enum.StrEnum):
    scheduled = "scheduled"
    live = "live"
    finished = "finished"


class Fixture(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "fixtures"
    __table_args__ = (
        # Idempotency key for ingestion: one fixture per (league, season,
        # pairing, kickoff). Re-ingesting the same CSV is a no-op.
        UniqueConstraint(
            "league_id",
            "season",
            "home_team_id",
            "away_team_id",
            "kickoff_at",
            name="uq_fixture_identity",
        ),
    )

    league_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leagues.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    season: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    home_team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    away_team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[FixtureStatus] = mapped_column(
        Enum(FixtureStatus, name="fixture_status"),
        default=FixtureStatus.finished,
        nullable=False,
    )
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)

    ft_home: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ft_away: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ht_home: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ht_away: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FixtureStats(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "fixture_stats"

    fixture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    home_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_corners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_corners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_xg_provider: Mapped[Decimal | None] = mapped_column(Numeric(5, 3), nullable=True)
    away_xg_provider: Mapped[Decimal | None] = mapped_column(Numeric(5, 3), nullable=True)


class Shot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Shot-level events feeding the own-xG model (populated in Phase 4)."""

    __tablename__ = "shots"

    fixture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), index=True, nullable=False
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    x: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    y: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    shot_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
