"""timescale hypertables: odds, predictions_live

Revision ID: 0003_timescale_hypertables
Revises: 0002_domain_core
Create Date: 2026-07-13

Creates the two time-series tables and converts them to Timescale hypertables.
The conversion is guarded by extension availability: on a Timescale image
(CI and prod) they become real hypertables; on a bare PostgreSQL they remain
ordinary tables so the schema is still usable in minimal environments. Either
way the primary key includes the time column, as Timescale requires.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_timescale_hypertables"
down_revision: str | None = "0002_domain_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timescale_available(bind: sa.engine.Connection) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    has_timescale = _timescale_available(bind)
    if has_timescale:
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "odds",
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("bookmaker", sa.String(length=32), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column("is_closing", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("fixture_id", "bookmaker", "market", "outcome", "ts"),
    )

    op.create_table(
        "predictions_live",
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("probability", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "fixture_id", "method", "market", "outcome", "minute", "recorded_at"
        ),
    )

    if has_timescale:
        op.execute("SELECT create_hypertable('odds', 'ts', if_not_exists => TRUE)")
        op.execute(
            "SELECT create_hypertable('predictions_live', 'recorded_at', if_not_exists => TRUE)"
        )


def downgrade() -> None:
    op.drop_table("predictions_live")
    op.drop_table("odds")
