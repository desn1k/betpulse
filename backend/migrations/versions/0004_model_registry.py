"""model registry + snapshots (governance §16)

Revision ID: 0004_model_registry
Revises: 0003_timescale_hypertables
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_model_registry"
down_revision: str | None = "0003_timescale_hypertables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

model_status = postgresql.ENUM("challenger", "champion", "retired", name="model_status")


def upgrade() -> None:
    model_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "model_registry",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("mlflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("accuracy_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("brier", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("log_loss", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("roi_vs_closing", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "challenger", "champion", "retired", name="model_status", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("is_visible", sa.Boolean(), nullable=False),
        sa.Column("display_weight", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("min_samples", sa.Integer(), nullable=False),
        sa.Column("last_trained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("method", "version", name="uq_registry_method_version"),
    )
    op.create_index(op.f("ix_model_registry_method"), "model_registry", ["method"])

    op.create_table(
        "model_registry_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "taken_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index(
        op.f("ix_model_registry_snapshots_taken_at"), "model_registry_snapshots", ["taken_at"]
    )


def downgrade() -> None:
    op.drop_table("model_registry_snapshots")
    op.drop_table("model_registry")
    model_status.drop(op.get_bind(), checkfirst=True)
