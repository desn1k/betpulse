"""backtester: strategies + precomputed feature store (Phase 9)

Revision ID: 0009_backtester
Revises: 0008_promos
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_backtester"
down_revision: str | None = "0008_promos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Backtester save/export are tier feature flags (spec §7 table). Patch the
    # tier rows seeded by 0007 (their point-in-time flags predate the backtester).
    op.execute(
        "UPDATE tiers SET feature_flags = feature_flags || "
        '\'{"backtester_save": false, "backtester_export": false}\'::jsonb '
        "WHERE name IN ('guest', 'free', 'pro')"
    )
    op.execute(
        "UPDATE tiers SET feature_flags = feature_flags || "
        '\'{"backtester_save": true, "backtester_export": true}\'::jsonb '
        "WHERE name = 'expert'"
    )

    op.create_table(
        "strategies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("filters", postgresql.JSONB(), nullable=False),
        sa.Column("bet_type", sa.String(length=16), nullable=False),
        sa.Column("pick", sa.String(length=8), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_strategies_user_id"), "strategies", ["user_id"])

    op.create_table(
        "backtest_features",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("league_id", sa.Uuid(), nullable=False),
        sa.Column("league_code", sa.String(length=32), nullable=False),
        sa.Column("season", sa.String(length=16), nullable=False),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_team_id", sa.Uuid(), nullable=False),
        sa.Column("away_team_id", sa.Uuid(), nullable=False),
        sa.Column("home_team", sa.String(length=128), nullable=False),
        sa.Column("away_team", sa.String(length=128), nullable=False),
        sa.Column("ft_home", sa.Integer(), nullable=False),
        sa.Column("ft_away", sa.Integer(), nullable=False),
        sa.Column("total_goals", sa.Integer(), nullable=False),
        sa.Column("ht_home", sa.Integer(), nullable=True),
        sa.Column("ht_away", sa.Integer(), nullable=True),
        sa.Column("elo_home", sa.Numeric(8, 3), nullable=False),
        sa.Column("elo_away", sa.Numeric(8, 3), nullable=False),
        sa.Column("elo_diff", sa.Numeric(8, 3), nullable=False),
        sa.Column("rolling_xg_home", sa.Numeric(6, 3), nullable=False),
        sa.Column("rolling_xg_away", sa.Numeric(6, 3), nullable=False),
        sa.Column("avg_total", sa.Numeric(6, 3), nullable=False),
        sa.Column("rest_days_home", sa.Numeric(6, 2), nullable=False),
        sa.Column("rest_days_away", sa.Numeric(6, 2), nullable=False),
        sa.Column("form_home", sa.Numeric(6, 3), nullable=False),
        sa.Column("form_away", sa.Numeric(6, 3), nullable=False),
        sa.Column("odds_home", sa.Numeric(8, 3), nullable=True),
        sa.Column("odds_draw", sa.Numeric(8, 3), nullable=True),
        sa.Column("odds_away", sa.Numeric(8, 3), nullable=True),
        sa.Column("odds_over", sa.Numeric(8, 3), nullable=True),
        sa.Column("odds_under", sa.Numeric(8, 3), nullable=True),
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
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["home_team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["away_team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("fixture_id", name="uq_backtest_feature_fixture"),
    )
    op.create_index(op.f("ix_backtest_features_fixture_id"), "backtest_features", ["fixture_id"])
    op.create_index(op.f("ix_backtest_features_league_id"), "backtest_features", ["league_id"])
    op.create_index(op.f("ix_backtest_features_league_code"), "backtest_features", ["league_code"])
    op.create_index(op.f("ix_backtest_features_season"), "backtest_features", ["season"])
    op.create_index(op.f("ix_backtest_features_kickoff_at"), "backtest_features", ["kickoff_at"])
    op.create_index(op.f("ix_backtest_features_elo_diff"), "backtest_features", ["elo_diff"])


def downgrade() -> None:
    op.drop_table("backtest_features")
    op.drop_index(op.f("ix_strategies_user_id"), table_name="strategies")
    op.drop_table("strategies")
    op.execute(
        "UPDATE tiers SET feature_flags = feature_flags - 'backtester_save' - 'backtester_export'"
    )
