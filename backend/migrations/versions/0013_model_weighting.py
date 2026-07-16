"""consensus weighting mode singleton (Phase 12b)

Revision ID: 0013_model_weighting
Revises: 0012_ingestion_runs
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_model_weighting"
down_revision: str | None = "0012_ingestion_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODE = sa.Enum("auto", "manual", name="weighting_mode")


def upgrade() -> None:
    op.create_table(
        "model_weighting",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("singleton", sa.String(length=16), nullable=False),
        sa.Column("mode", _MODE, server_default="auto", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("singleton", name="uq_model_weighting_singleton"),
    )


def downgrade() -> None:
    op.drop_table("model_weighting")
    sa.Enum(name="weighting_mode").drop(op.get_bind())
