"""provider-agnostic identity: external refs, season format, team key (HI-2)

Schema only (data fixes are 0015):

* ``fixture_external_refs`` — a source's own fixture id, unique per provider;
* ``fixtures.kickoff_time_known`` — false for date-only sources;
* ``leagues.season_start_month`` — split-year (``YYYY-YYYY``) vs calendar-year
  (``YYYY``) season labels, seeded for the leagues we cover;
* ``provider_team_aliases.external_id`` — a provider's stable team id;
* ``provider_unmapped_teams`` — the worklist of names a strict provider sent
  that no alias resolves;
* teams are unique per ``(country, normalized_name)`` instead of
  ``normalized_name`` alone.

Alembic runs the whole upgrade in one transaction (``migrations/env.py``), so a
stop rolls everything back. Any pre-check that finds conflicting rows raises
with the list instead of merging anything.

Revision ID: 0014_identity_schema
Revises: 0013_model_weighting
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_identity_schema"
down_revision: str | None = "0013_model_weighting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Leagues whose season spans two calendar years, and the month it starts.
SEASON_START_MONTH = {
    "EPL": 8,
    "LALIGA": 8,
    "SERIEA": 8,
    "BUNDESLIGA": 8,
    "LIGUE1": 8,
    "RPL": 7,
}


def _stop_on_conflicts(title: str, rows: list[sa.Row[tuple[object, ...]]]) -> None:
    if not rows:
        return
    lines = [" | ".join(str(v) for v in row) for row in rows[:50]]
    more = f"\n... and {len(rows) - 50} more" if len(rows) > 50 else ""
    raise RuntimeError(
        f"STOP: {title} ({len(rows)} conflicting groups). Nothing was changed; resolve "
        f"these rows and re-run:\n" + "\n".join(lines) + more
    )


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("leagues", sa.Column("season_start_month", sa.SmallInteger(), nullable=True))
    for code, month in SEASON_START_MONTH.items():
        bind.execute(
            sa.text("UPDATE leagues SET season_start_month = :m WHERE code = :c"),
            {"m": month, "c": code},
        )

    # The new team key is strictly weaker than the old one, so existing rows
    # cannot conflict — checked anyway, as every key change here is.
    _stop_on_conflicts(
        "teams would collide on (country, normalized_name)",
        list(
            bind.execute(
                sa.text(
                    "SELECT country, normalized_name, count(*) FROM teams "
                    "GROUP BY country, normalized_name HAVING count(*) > 1"
                )
            )
        ),
    )
    op.drop_index("ix_teams_normalized_name", table_name="teams")
    op.create_index("ix_teams_normalized_name", "teams", ["normalized_name"], unique=False)
    op.create_unique_constraint(
        "uq_team_country_name",
        "teams",
        ["country", "normalized_name"],
        postgresql_nulls_not_distinct=True,
    )

    op.add_column(
        "provider_team_aliases", sa.Column("external_id", sa.String(length=128), nullable=True)
    )
    op.create_unique_constraint(
        "uq_team_alias_external", "provider_team_aliases", ["provider", "external_id"]
    )

    op.create_table(
        "provider_unmapped_teams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("raw_name", sa.String(length=128), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("league_hint", sa.String(length=64), nullable=True),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
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
        sa.UniqueConstraint("provider", "raw_name", name="uq_unmapped_team"),
    )
    op.create_index("ix_provider_unmapped_teams_provider", "provider_unmapped_teams", ["provider"])

    op.add_column(
        "fixtures",
        sa.Column("kickoff_time_known", sa.Boolean(), server_default=sa.true(), nullable=False),
    )

    op.create_table(
        "fixture_external_refs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "fixture_id",
            sa.Uuid(),
            sa.ForeignKey("fixtures.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("provider", "external_id", name="uq_fixture_external_ref"),
    )
    op.create_index("ix_fixture_external_refs_fixture_id", "fixture_external_refs", ["fixture_id"])


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_fixture_external_refs_fixture_id", table_name="fixture_external_refs")
    op.drop_table("fixture_external_refs")
    op.drop_column("fixtures", "kickoff_time_known")
    op.drop_index("ix_provider_unmapped_teams_provider", table_name="provider_unmapped_teams")
    op.drop_table("provider_unmapped_teams")
    op.drop_constraint("uq_team_alias_external", "provider_team_aliases", type_="unique")
    op.drop_column("provider_team_aliases", "external_id")

    # Going back to a global name key can conflict (same name, two countries).
    _stop_on_conflicts(
        "teams would collide on normalized_name alone",
        list(
            bind.execute(
                sa.text(
                    "SELECT normalized_name, string_agg(coalesce(country, '-'), ', '), "
                    "count(*) FROM teams GROUP BY normalized_name HAVING count(*) > 1"
                )
            )
        ),
    )
    op.drop_constraint("uq_team_country_name", "teams", type_="unique")
    op.drop_index("ix_teams_normalized_name", table_name="teams")
    op.create_index("ix_teams_normalized_name", "teams", ["normalized_name"], unique=True)
    op.drop_column("leagues", "season_start_month")
