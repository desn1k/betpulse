"""Per-team rating history (Elo, Glicko-2). Populated in Phase 4."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class RatingElo(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ratings_elo"
    __table_args__ = (UniqueConstraint("team_id", "as_of", name="uq_elo_team_date"),)

    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    as_of: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    rating: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)


class RatingGlicko(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ratings_glicko"
    __table_args__ = (UniqueConstraint("team_id", "as_of", name="uq_glicko_team_date"),)

    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    as_of: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    rating: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    rd: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    volatility: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
