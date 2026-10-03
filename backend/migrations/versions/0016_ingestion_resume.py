"""resumable ingestion runs + cross-source conflicts (HI-3)

* ``ingestion_runs.content_sha256`` / ``records`` / ``skipped_reason`` — a
  re-run skips a (league, season) whose payload hashes the same as the last
  successful run;
* ``ingestion_conflicts`` — a source disagreeing with the value another source
  stored for the same fixture (recorded, never applied; ``data-report`` shows
  them).

Schema only, no data change; runs in the single upgrade transaction.

Revision ID: 0016_ingestion_resume
Revises: 0015_identity_data
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_ingestion_resume"
down_revision: str | None = "0015_identity_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_runs", sa.Column("content_sha256", sa.String(length=64), nullable=True)
    )
    op.add_column("ingestion_runs", sa.Column("records", sa.Integer(), nullable=True))
    op.add_column(
        "ingestion_runs", sa.Column("skipped_reason", sa.String(length=64), nullable=True)
    )

    op.create_table(
        "ingestion_conflicts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "fixture_id",
            sa.Uuid(),
            sa.ForeignKey("fixtures.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=160), nullable=True),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("existing", sa.String(length=64), nullable=True),
        sa.Column("incoming", sa.String(length=64), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("fixture_id", "provider", "field", name="uq_ingestion_conflict"),
    )
    op.create_index("ix_ingestion_conflicts_fixture_id", "ingestion_conflicts", ["fixture_id"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_conflicts_fixture_id", table_name="ingestion_conflicts")
    op.drop_table("ingestion_conflicts")
    op.drop_column("ingestion_runs", "skipped_reason")
    op.drop_column("ingestion_runs", "records")
    op.drop_column("ingestion_runs", "content_sha256")
