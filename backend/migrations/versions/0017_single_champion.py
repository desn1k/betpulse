"""one global champion, enforced by the database (model governance)

A partial unique index on ``model_registry (status) WHERE status = 'champion'``
makes a second champion row impossible, whatever code path tries it.

Rows that already break the rule are **not** fixed here: choosing which one
stays champion is a governance decision. The upgrade checks first and, if more
than one champion exists, STOPS with the list. The upgrade runs as one
transaction, so nothing changes; demote the extra rows in the admin panel (or
roll back to a registry snapshot) and re-run.

Revision ID: 0017_single_champion
Revises: 0016_ingestion_resume
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_single_champion"
down_revision: str | None = "0016_ingestion_resume"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_model_registry_single_champion"


def upgrade() -> None:
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id, method, version, last_evaluated_at FROM model_registry "
                "WHERE status = 'champion' ORDER BY method, version"
            )
        )
        .all()
    )
    if len(rows) > 1:
        lines = [" | ".join(str(v) for v in row) for row in rows]
        raise RuntimeError(
            f"STOP: {len(rows)} champion rows in model_registry (id | method | version | "
            "last_evaluated_at). Nothing was changed; demote all but one (admin panel or a "
            "registry snapshot rollback) and re-run:\n" + "\n".join(lines)
        )
    op.create_index(
        INDEX,
        "model_registry",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'champion'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="model_registry")
