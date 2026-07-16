"""ingestion run log (Phase 12a)

Revision ID: 0012_ingestion_runs
Revises: 0011_push
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_ingestion_runs"
down_revision: str | None = "0011_push"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS = sa.Enum("running", "success", "partial", "failed", name="ingestion_status")


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("league", sa.String(length=32), nullable=True),
        sa.Column("season", sa.String(length=16), nullable=True),
        sa.Column("status", _STATUS, server_default="running", nullable=False),
        sa.Column("fixtures_ingested", sa.Integer(), server_default="0", nullable=False),
        sa.Column("odds_ingested", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.create_index(op.f("ix_ingestion_runs_status"), "ingestion_runs", ["status"])
    op.create_index(op.f("ix_ingestion_runs_started_at"), "ingestion_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_ingestion_runs_started_at"), table_name="ingestion_runs")
    op.drop_index(op.f("ix_ingestion_runs_status"), table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    sa.Enum(name="ingestion_status").drop(op.get_bind())
