"""provider-agnostic identity: canonical seasons, UTC kickoffs, external refs (HI-2)

Data only, in this order, each step pre-checked for conflicts first:

1. **Canonical seasons** (``app.core.seasons``): a split-year league's
   ``"2026"`` / ``"2026/2027"`` / ``"2026/27"`` becomes ``"2026-2027"`` in
   ``fixtures``, ``backtest_features`` and ``ingestion_runs``. A label that
   cannot be read is left as is (``data-report`` flags it).
2. **football-data kickoffs**: rows were stored with the CSV's UK local time
   labelled as UTC, and date-only rows at 00:00. Now: date-only → 12:00 UTC of
   that date with ``kickoff_time_known = false``; otherwise the wall-clock is
   read as Europe/London and converted to UTC. Their odds rows that sat at the
   old kickoff move to the new one (closing quotes are stored at kickoff).
3. **External refs** for those rows: ``fd:{div}:{season}:{YYYYMMDD}:{home}:{away}``
   — exactly what the football-data adapter now computes, so a re-run of the
   same CSV finds them instead of inserting.

If any step would make two fixtures (or two odds rows) collide, the migration
raises with the list and — the upgrade being one transaction — nothing changes.
Never merges.

Downgrade restores the old kickoff convention for football-data rows; season
labels stay canonical (the original spellings are not recoverable).

Revision ID: 0015_identity_data
Revises: 0014_identity_schema
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op
from app.core.seasons import SeasonFormatError, canonical_season

revision: str = "0015_identity_data"
down_revision: str | None = "0014_identity_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FOOTBALL_DATA = "football_data_couk"
# Conversions run in Python (zoneinfo + the tzdata package), not in SQL: the
# database server's own time-zone files are not something to depend on.
_UK = ZoneInfo("Europe/London")
_NOON = timedelta(hours=12)
FD_DIVISION = {"EPL": "E0", "LALIGA": "SP1", "SERIEA": "I1", "BUNDESLIGA": "D1", "LIGUE1": "F1"}


def _stop_on_conflicts(title: str, rows: list[sa.Row[tuple[object, ...]]]) -> None:
    if not rows:
        return
    lines = [" | ".join(str(v) for v in row) for row in rows[:50]]
    more = f"\n... and {len(rows) - 50} more" if len(rows) > 50 else ""
    raise RuntimeError(
        f"STOP: {title} ({len(rows)} conflicts). Nothing was changed; resolve these rows "
        f"(see `make data-report`) and re-run:\n" + "\n".join(lines) + more
    )


def _canonical_seasons(bind: sa.Connection) -> None:
    labels = bind.execute(
        sa.text(
            "SELECT DISTINCT f.league_id, l.code, f.season, l.season_start_month "
            "FROM fixtures f JOIN leagues l ON l.id = f.league_id"
        )
    ).all()
    mapping: list[dict[str, object]] = []
    for league_id, code, season, start_month in labels:
        try:
            target = canonical_season(season, split_year=start_month is not None)
        except SeasonFormatError:
            continue  # left for data-report to flag
        if target != season:
            mapping.append({"league_id": league_id, "code": code, "old": season, "new": target})
    if not mapping:
        return

    bind.execute(
        sa.text(
            "CREATE TEMP TABLE season_map (league_id uuid, code text, old text, new text) "
            "ON COMMIT DROP"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO season_map (league_id, code, old, new) "
            "VALUES (:league_id, :code, :old, :new)"
        ),
        mapping,
    )
    _stop_on_conflicts(
        "relabelled seasons would duplicate fixtures (league, season, pairing, kickoff)",
        list(
            bind.execute(
                sa.text(
                    """
                    WITH target AS (
                        SELECT f.id, f.league_id, coalesce(m.new, f.season) AS season,
                               f.home_team_id, f.away_team_id, f.kickoff_at
                        FROM fixtures f
                        LEFT JOIN season_map m
                          ON m.league_id = f.league_id AND m.old = f.season
                    )
                    SELECT league_id, season, home_team_id, away_team_id, kickoff_at,
                           string_agg(id::text, ', ')
                    FROM target
                    GROUP BY league_id, season, home_team_id, away_team_id, kickoff_at
                    HAVING count(*) > 1
                    """
                )
            )
        ),
    )
    bind.execute(
        sa.text(
            "UPDATE fixtures f SET season = m.new FROM season_map m "
            "WHERE f.league_id = m.league_id AND f.season = m.old"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE backtest_features b SET season = m.new FROM season_map m "
            "WHERE b.league_id = m.league_id AND b.season = m.old"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE ingestion_runs r SET season = m.new FROM season_map m "
            "WHERE r.league = m.code AND r.season = m.old"
        )
    )


def _corrected(stored: datetime) -> tuple[datetime, bool]:
    """Old convention → new: the stored UTC wall-clock was really UK local
    time; 00:00 meant "date only" (→ 12:00 UTC, time unknown)."""
    wall = stored.astimezone(UTC).replace(tzinfo=None)
    if wall.time() == time(0, 0):
        return wall.replace(tzinfo=UTC) + _NOON, False
    return wall.replace(tzinfo=_UK).astimezone(UTC), True


def _original(current: datetime, time_known: bool) -> datetime:
    """Inverse of :func:`_corrected` (downgrade)."""
    if not time_known:
        return current.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    wall = current.astimezone(_UK).replace(tzinfo=None)
    return wall.replace(tzinfo=UTC)


def _football_data_kickoffs(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text("SELECT id, kickoff_at FROM fixtures WHERE source = :fd"), {"fd": FOOTBALL_DATA}
    ).all()
    bind.execute(
        sa.text(
            "CREATE TEMP TABLE kickoff_map (fixture_id uuid PRIMARY KEY, "
            "old_kickoff timestamptz, new_kickoff timestamptz, time_known boolean) "
            "ON COMMIT DROP"
        )
    )
    if not rows:
        return
    mapping = []
    for fixture_id, kickoff in rows:
        new_kickoff, known = _corrected(kickoff)
        mapping.append({"f": fixture_id, "o": kickoff, "n": new_kickoff, "k": known})
    bind.execute(
        sa.text(
            "INSERT INTO kickoff_map (fixture_id, old_kickoff, new_kickoff, time_known) "
            "VALUES (:f, :o, :n, :k)"
        ),
        mapping,
    )
    _stop_on_conflicts(
        "corrected kickoffs would duplicate fixtures (league, season, pairing, kickoff)",
        list(
            bind.execute(
                sa.text(
                    """
                    WITH target AS (
                        SELECT f.id, f.league_id, f.season, f.home_team_id, f.away_team_id,
                               coalesce(k.new_kickoff, f.kickoff_at) AS kickoff_at
                        FROM fixtures f LEFT JOIN kickoff_map k ON k.fixture_id = f.id
                    )
                    SELECT league_id, season, home_team_id, away_team_id, kickoff_at,
                           string_agg(id::text, ', ')
                    FROM target
                    GROUP BY league_id, season, home_team_id, away_team_id, kickoff_at
                    HAVING count(*) > 1
                    """
                )
            )
        ),
    )
    _stop_on_conflicts(
        "moved closing quotes would collide with existing odds rows",
        list(
            bind.execute(
                sa.text(
                    """
                    SELECT o.fixture_id, o.bookmaker, o.market, o.outcome, k.new_kickoff
                    FROM odds o
                    JOIN kickoff_map k
                      ON k.fixture_id = o.fixture_id AND o.ts = k.old_kickoff
                    WHERE k.new_kickoff <> k.old_kickoff
                      AND EXISTS (
                        SELECT 1 FROM odds x
                        WHERE x.fixture_id = o.fixture_id AND x.bookmaker = o.bookmaker
                          AND x.market = o.market AND x.outcome = o.outcome
                          AND x.ts = k.new_kickoff
                      )
                    """
                )
            )
        ),
    )

    bind.execute(
        sa.text(
            "UPDATE fixtures f SET kickoff_at = k.new_kickoff, "
            "kickoff_time_known = k.time_known "
            "FROM kickoff_map k WHERE f.id = k.fixture_id"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE backtest_features b SET kickoff_at = k.new_kickoff "
            "FROM kickoff_map k WHERE b.fixture_id = k.fixture_id"
        )
    )
    # ``ts`` is the hypertable's time column: move rows by insert + delete
    # rather than updating the partitioning key in place.
    bind.execute(
        sa.text(
            """
            INSERT INTO odds (fixture_id, bookmaker, market, outcome, ts, price, is_closing)
            SELECT o.fixture_id, o.bookmaker, o.market, o.outcome, k.new_kickoff, o.price,
                   o.is_closing
            FROM odds o
            JOIN kickoff_map k ON k.fixture_id = o.fixture_id AND o.ts = k.old_kickoff
            WHERE k.new_kickoff <> k.old_kickoff
            """
        )
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM odds o USING kickoff_map k
            WHERE o.fixture_id = k.fixture_id AND o.ts = k.old_kickoff
              AND k.new_kickoff <> k.old_kickoff
            """
        )
    )

    # External refs as the adapter computes them (date = the CSV's date, i.e.
    # the old wall-clock date).
    division = " ".join(f"WHEN '{code}' THEN '{div}'" for code, div in FD_DIVISION.items())
    bind.execute(
        sa.text(
            f"""
            INSERT INTO fixture_external_refs (id, fixture_id, provider, external_id)
            SELECT gen_random_uuid(), f.id, :fd,
                   'fd:' || (CASE l.code {division} ELSE l.code END) || ':' || f.season || ':'
                   || to_char(k.old_kickoff AT TIME ZONE 'UTC', 'YYYYMMDD') || ':'
                   || h.normalized_name || ':' || a.normalized_name
            FROM kickoff_map k
            JOIN fixtures f ON f.id = k.fixture_id
            JOIN leagues l ON l.id = f.league_id
            JOIN teams h ON h.id = f.home_team_id
            JOIN teams a ON a.id = f.away_team_id
            ON CONFLICT ON CONSTRAINT uq_fixture_external_ref DO NOTHING
            """  # noqa: S608 - the CASE is built from the module constant above
        ),
        {"fd": FOOTBALL_DATA},
    )


def upgrade() -> None:
    bind = op.get_bind()
    _canonical_seasons(bind)
    _football_data_kickoffs(bind)


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, kickoff_at, kickoff_time_known FROM fixtures WHERE source = :fd"),
        {"fd": FOOTBALL_DATA},
    ).all()
    bind.execute(
        sa.text(
            "CREATE TEMP TABLE kickoff_back (fixture_id uuid PRIMARY KEY, "
            "cur_kickoff timestamptz, old_kickoff timestamptz) ON COMMIT DROP"
        )
    )
    if rows:
        bind.execute(
            sa.text(
                "INSERT INTO kickoff_back (fixture_id, cur_kickoff, old_kickoff) "
                "VALUES (:f, :c, :o)"
            ),
            [{"f": f, "c": k, "o": _original(k, known)} for f, k, known in rows],
        )
    bind.execute(
        sa.text(
            "UPDATE fixtures f SET kickoff_at = k.old_kickoff "
            "FROM kickoff_back k WHERE f.id = k.fixture_id"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE backtest_features b SET kickoff_at = k.old_kickoff "
            "FROM kickoff_back k WHERE b.fixture_id = k.fixture_id"
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO odds (fixture_id, bookmaker, market, outcome, ts, price, is_closing)
            SELECT o.fixture_id, o.bookmaker, o.market, o.outcome, k.old_kickoff, o.price,
                   o.is_closing
            FROM odds o
            JOIN kickoff_back k ON k.fixture_id = o.fixture_id AND o.ts = k.cur_kickoff
            WHERE k.old_kickoff <> k.cur_kickoff
            ON CONFLICT DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM odds o USING kickoff_back k
            WHERE o.fixture_id = k.fixture_id AND o.ts = k.cur_kickoff
              AND k.old_kickoff <> k.cur_kickoff
            """
        )
    )
    bind.execute(
        sa.text("DELETE FROM fixture_external_refs WHERE provider = :fd"), {"fd": FOOTBALL_DATA}
    )
