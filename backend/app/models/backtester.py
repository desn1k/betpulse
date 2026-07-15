"""Backtester: saved strategies + a precomputed per-fixture feature store (§6).

``backtest_features`` is the precomputed store the strategy filters query — one
indexed row per finished fixture with its as-of features (Elo, rolling xG, rest
days, form — computed chronologically, no leakage) and its closing-odds snapshot
(1X2 + over/under 2.5). Filters hit this table with bound parameters instead of
joining five tables at query time. Population is idempotent (upsert by
``fixture_id``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Strategy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategies"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Whitelisted filter fields (validated by app.schemas.backtester.StrategyFilter).
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    bet_type: Mapped[str] = mapped_column(String(16), nullable=False)
    pick: Mapped[str] = mapped_column(String(8), nullable=False)


class BacktestFeature(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "backtest_features"
    __table_args__ = (UniqueConstraint("fixture_id", name="uq_backtest_feature_fixture"),)

    fixture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), index=True, nullable=False
    )
    league_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), index=True, nullable=False
    )
    league_code: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    season: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    kickoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )

    home_team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    away_team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised names so the CSV export needs no team join (and no UUIDs leak).
    home_team: Mapped[str] = mapped_column(String(128), nullable=False)
    away_team: Mapped[str] = mapped_column(String(128), nullable=False)

    ft_home: Mapped[int] = mapped_column(Integer, nullable=False)
    ft_away: Mapped[int] = mapped_column(Integer, nullable=False)
    total_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    ht_home: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ht_away: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # As-of features (leakage-free; see app.ml.features.build_feature_table).
    elo_home: Mapped[float] = mapped_column(Numeric(8, 3), nullable=False)
    elo_away: Mapped[float] = mapped_column(Numeric(8, 3), nullable=False)
    elo_diff: Mapped[float] = mapped_column(Numeric(8, 3), index=True, nullable=False)
    rolling_xg_home: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    rolling_xg_away: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    avg_total: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    rest_days_home: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    rest_days_away: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    form_home: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    form_away: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)

    # Closing-odds snapshot (null when the market is absent for this fixture).
    odds_home: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    odds_draw: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    odds_away: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    odds_over: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    odds_under: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
