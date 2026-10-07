"""LLM analysis cache per language + an append-only generation journal (ER-H-02)

The analysis cache was unique per ``(fixture_id, model)`` and ignored the
language, so a request in one language could be served the other's text; its
upsert also overwrote the row, and the spend dashboard, which summed those rows,
lost every earlier generation.

Upgrade:
- ``llm_analyses`` becomes unique per ``(fixture_id, model, language)``;
- ``llm_generations`` keeps one row per LLM call (tokens and the cost computed at
  write time); spend is read from it;
- the journal is back-filled with one row per cached analysis. Generations
  overwritten before this migration are gone and cannot be recovered.

Downgrade keeps the **newest** analysis per ``(fixture_id, model)``
(``created_at DESC, id DESC``) and deletes the others, so the old constraint can
come back, then drops the journal. A NOTICE gives both counts; env.py forwards it
to the alembic log. Take a ``pg_dump`` of ``llm_generations`` (and
``llm_analyses``) first: the journal is the only spend history (HANDOFF §9i).

Revision ID: 0018_llm_generations
Revises: 0017_single_champion
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_llm_generations"
down_revision: str | None = "0017_single_champion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_UNIQUE = "uq_llm_analysis_fixture_model"
NEW_UNIQUE = "uq_llm_analysis_fixture_model_language"


def upgrade() -> None:
    op.drop_constraint(OLD_UNIQUE, "llm_analyses", type_="unique")
    op.create_unique_constraint(NEW_UNIQUE, "llm_analyses", ["fixture_id", "model", "language"])

    op.create_table(
        "llm_generations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_llm_generations_fixture_id"), "llm_generations", ["fixture_id"])
    op.create_index(op.f("ix_llm_generations_created_at"), "llm_generations", ["created_at"])

    # One journal row per cached analysis: the only generations still known.
    op.execute(
        "INSERT INTO llm_generations "
        "(id, fixture_id, model, language, tokens_in, tokens_out, cost, created_at) "
        "SELECT gen_random_uuid(), fixture_id, model, language, tokens_in, tokens_out, cost, "
        "created_at FROM llm_analyses"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
          deleted bigint;
          journal bigint;
        BEGIN
          SELECT count(*) INTO journal FROM llm_generations;
          DELETE FROM llm_analyses a
          USING (
            SELECT id, row_number() OVER (
              PARTITION BY fixture_id, model ORDER BY created_at DESC, id DESC
            ) AS rn
            FROM llm_analyses
          ) ranked
          WHERE a.id = ranked.id AND ranked.rn > 1;
          GET DIAGNOSTICS deleted = ROW_COUNT;
          RAISE NOTICE '0018 downgrade: % llm_analyses rows deleted, % llm_generations rows dropped',
            deleted, journal;
        END $$;
        """
    )
    op.drop_index(op.f("ix_llm_generations_created_at"), table_name="llm_generations")
    op.drop_index(op.f("ix_llm_generations_fixture_id"), table_name="llm_generations")
    op.drop_table("llm_generations")
    op.drop_constraint(NEW_UNIQUE, "llm_analyses", type_="unique")
    op.create_unique_constraint(OLD_UNIQUE, "llm_analyses", ["fixture_id", "model"])
