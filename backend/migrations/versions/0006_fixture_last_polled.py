"""fixture last_polled_at (Phase 6 data-freshness signal)

Revision ID: 0006_fixture_last_polled
Revises: 0005_live_push
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_fixture_last_polled"
down_revision: str | None = "0005_live_push"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: existing (historically-loaded) fixtures have never been polled,
    # and no server_default so a schedule row that is never live stays null.
    op.add_column(
        "fixtures",
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fixtures", "last_polled_at")
