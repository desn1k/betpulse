"""Bookmaker odds — a Timescale hypertable (time-series of prices).

The primary key includes the ``ts`` partitioning column, as Timescale requires
any unique constraint on a hypertable to contain the time dimension. Closing
odds from football-data.co.uk are stored with ``ts = kickoff`` and
``is_closing = True``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Odds(Base):
    __tablename__ = "odds"

    fixture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), primary_key=True
    )
    bookmaker: Mapped[str] = mapped_column(String(32), primary_key=True)
    market: Mapped[str] = mapped_column(String(32), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(16), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    price: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    is_closing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
