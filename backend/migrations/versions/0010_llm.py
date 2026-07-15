"""llm config + analyses + fixture llm rank (Phase 10)

Revision ID: 0010_llm
Revises: 0009_backtester
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_llm"
down_revision: str | None = "0009_backtester"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fixtures", sa.Column("fixture_llm_rank", sa.Integer(), nullable=True))

    op.create_table(
        "llm_config",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("singleton", sa.String(length=16), nullable=False),
        sa.Column("base_url", sa.String(length=256), server_default="", nullable=False),
        sa.Column("model", sa.String(length=128), server_default="", nullable=False),
        sa.Column("encrypted_key", sa.String(length=512), nullable=True),
        sa.Column("key_suffix", sa.String(length=8), nullable=True),
        sa.Column("max_tokens", sa.Integer(), server_default="600", nullable=False),
        sa.Column("daily_token_budget", sa.Integer(), server_default="100000", nullable=False),
        sa.Column("cache_ttl_seconds", sa.Integer(), server_default="86400", nullable=False),
        sa.Column("cost_per_1k_in", sa.Numeric(10, 5), server_default="0", nullable=False),
        sa.Column("cost_per_1k_out", sa.Numeric(10, 5), server_default="0", nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.UniqueConstraint("singleton", name="uq_llm_config_singleton"),
    )

    op.create_table(
        "llm_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), server_default="", nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("language", sa.String(length=8), server_default="en", nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
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
        sa.UniqueConstraint("fixture_id", "model", name="uq_llm_analysis_fixture_model"),
    )
    op.create_index(op.f("ix_llm_analyses_fixture_id"), "llm_analyses", ["fixture_id"])

    # LLM access is a tier feature flag (spec §7 table). Patch the tiers seeded by
    # 0007 (none = guest · match_of_day = free · top5 = pro · any = expert).
    for name, level in (
        ("guest", "none"),
        ("free", "match_of_day"),
        ("pro", "top5"),
        ("expert", "any"),
    ):
        op.execute(
            sa.text(
                "UPDATE tiers SET feature_flags = "
                "feature_flags || jsonb_build_object('llm', :level) WHERE name = :name"
            ).bindparams(level=level, name=name)
        )


def downgrade() -> None:
    op.execute("UPDATE tiers SET feature_flags = feature_flags - 'llm'")
    op.drop_index(op.f("ix_llm_analyses_fixture_id"), table_name="llm_analyses")
    op.drop_table("llm_analyses")
    op.drop_table("llm_config")
    op.drop_column("fixtures", "fixture_llm_rank")
