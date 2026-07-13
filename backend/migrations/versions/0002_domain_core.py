"""domain core: reference, fixtures, ratings, predictions, provider accounts

Revision ID: 0002_domain_core
Revises: 0001_initial_auth
Create Date: 2026-07-13

Portable relational schema for the domain model. Timescale-specific tables
(odds, predictions_live hypertables) live in the next migration so this one
stays free of any extension dependency.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_domain_core"
down_revision: str | None = "0001_initial_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

fixture_status = postgresql.ENUM("scheduled", "live", "finished", name="fixture_status")

_TS = dict(server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    fixture_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "leagues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )
    op.create_index(op.f("ix_leagues_code"), "leagues", ["code"], unique=True)

    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=128), nullable=False),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )
    op.create_index(op.f("ix_teams_normalized_name"), "teams", ["normalized_name"], unique=True)

    op.create_table(
        "provider_team_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("alias", sa.String(length=128), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("provider", "alias", name="uq_team_alias"),
    )
    op.create_index(
        op.f("ix_provider_team_aliases_provider"), "provider_team_aliases", ["provider"]
    )
    op.create_index(op.f("ix_provider_team_aliases_team_id"), "provider_team_aliases", ["team_id"])

    op.create_table(
        "provider_league_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("alias", sa.String(length=128), nullable=False),
        sa.Column("league_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("provider", "alias", name="uq_league_alias"),
    )
    op.create_index(
        op.f("ix_provider_league_aliases_provider"), "provider_league_aliases", ["provider"]
    )
    op.create_index(
        op.f("ix_provider_league_aliases_league_id"), "provider_league_aliases", ["league_id"]
    )

    op.create_table(
        "provider_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("roles", postgresql.JSONB(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("encrypted_key", sa.String(length=512), nullable=True),
        sa.Column("key_suffix", sa.String(length=8), nullable=True),
        sa.Column("requests_per_minute", sa.Integer(), nullable=True),
        sa.Column("requests_per_day", sa.Integer(), nullable=True),
        sa.Column("quota_state", postgresql.JSONB(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
    )
    op.create_index(op.f("ix_provider_accounts_name"), "provider_accounts", ["name"], unique=True)

    op.create_table(
        "fixtures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("league_id", sa.Uuid(), nullable=False),
        sa.Column("season", sa.String(length=16), nullable=False),
        sa.Column("home_team_id", sa.Uuid(), nullable=False),
        sa.Column("away_team_id", sa.Uuid(), nullable=False),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "scheduled", "live", "finished", name="fixture_status", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("minute", sa.Integer(), nullable=True),
        sa.Column("ft_home", sa.Integer(), nullable=True),
        sa.Column("ft_away", sa.Integer(), nullable=True),
        sa.Column("ht_home", sa.Integer(), nullable=True),
        sa.Column("ht_away", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["home_team_id"], ["teams.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["away_team_id"], ["teams.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "league_id",
            "season",
            "home_team_id",
            "away_team_id",
            "kickoff_at",
            name="uq_fixture_identity",
        ),
    )
    op.create_index(op.f("ix_fixtures_league_id"), "fixtures", ["league_id"])
    op.create_index(op.f("ix_fixtures_season"), "fixtures", ["season"])
    op.create_index(op.f("ix_fixtures_home_team_id"), "fixtures", ["home_team_id"])
    op.create_index(op.f("ix_fixtures_away_team_id"), "fixtures", ["away_team_id"])

    op.create_table(
        "fixture_stats",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("home_shots", sa.Integer(), nullable=True),
        sa.Column("away_shots", sa.Integer(), nullable=True),
        sa.Column("home_shots_on_target", sa.Integer(), nullable=True),
        sa.Column("away_shots_on_target", sa.Integer(), nullable=True),
        sa.Column("home_corners", sa.Integer(), nullable=True),
        sa.Column("away_corners", sa.Integer(), nullable=True),
        sa.Column("home_xg_provider", sa.Numeric(precision=5, scale=3), nullable=True),
        sa.Column("away_xg_provider", sa.Numeric(precision=5, scale=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"),
    )
    op.create_index(
        op.f("ix_fixture_stats_fixture_id"), "fixture_stats", ["fixture_id"], unique=True
    )

    op.create_table(
        "shots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=True),
        sa.Column("x", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("y", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("shot_type", sa.String(length=32), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="RESTRICT"),
    )
    op.create_index(op.f("ix_shots_fixture_id"), "shots", ["fixture_id"])
    op.create_index(op.f("ix_shots_team_id"), "shots", ["team_id"])

    op.create_table(
        "ratings_elo",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("rating", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("team_id", "as_of", name="uq_elo_team_date"),
    )
    op.create_index(op.f("ix_ratings_elo_team_id"), "ratings_elo", ["team_id"])
    op.create_index(op.f("ix_ratings_elo_as_of"), "ratings_elo", ["as_of"])

    op.create_table(
        "ratings_glicko",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("rating", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column("rd", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column("volatility", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), **_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), **_TS),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("team_id", "as_of", name="uq_glicko_team_date"),
    )
    op.create_index(op.f("ix_ratings_glicko_team_id"), "ratings_glicko", ["team_id"])
    op.create_index(op.f("ix_ratings_glicko_as_of"), "ratings_glicko", ["as_of"])

    op.create_table(
        "predictions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("probability", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "fixture_id",
            "method",
            "market",
            "outcome",
            "model_version",
            name="uq_prediction_identity",
        ),
    )
    op.create_index(op.f("ix_predictions_fixture_id"), "predictions", ["fixture_id"])
    op.create_index(op.f("ix_predictions_method"), "predictions", ["method"])

    op.create_table(
        "model_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("mlflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_model_runs_method"), "model_runs", ["method"])


def downgrade() -> None:
    op.drop_table("model_runs")
    op.drop_table("predictions")
    op.drop_table("ratings_glicko")
    op.drop_table("ratings_elo")
    op.drop_table("shots")
    op.drop_table("fixture_stats")
    op.drop_table("fixtures")
    op.drop_table("provider_accounts")
    op.drop_table("provider_league_aliases")
    op.drop_table("provider_team_aliases")
    op.drop_table("teams")
    op.drop_table("leagues")
    fixture_status.drop(op.get_bind(), checkfirst=True)
