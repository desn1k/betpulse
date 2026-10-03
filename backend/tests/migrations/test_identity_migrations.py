"""Migrations 0014/0015 against real data in a throwaway database.

Each test creates its own database, migrates it to 0013, seeds rows the way the
old code wrote them, then upgrades. Conflicts must STOP the upgrade with a list
and leave the database exactly as it was (still at 0013) — never merge.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from app.core.config import get_settings

BACKEND = Path(__file__).resolve().parents[2]
FD = "football_data_couk"


def _dsn(database: str) -> str:
    base = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    return base.rsplit("/", 1)[0] + "/" + database


def _admin(sql: str) -> None:
    async def run() -> None:
        conn = await asyncpg.connect(_dsn("postgres"))
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(run())


def _query(database: str, sql: str, *args: Any) -> list[asyncpg.Record]:
    async def run() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(_dsn(database))
        try:
            return list(await conn.fetch(sql, *args))
        finally:
            await conn.close()

    return asyncio.run(run())


def _execute(database: str, sql: str, *args: Any) -> None:
    async def run() -> None:
        conn = await asyncpg.connect(_dsn(database))
        try:
            await conn.execute(sql, *args)
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.fixture
def migdb(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Config]]:
    name = f"bp_migtest_{uuid.uuid4().hex[:10]}"
    _admin(f'CREATE DATABASE "{name}"')
    settings = get_settings()
    monkeypatch.setattr(
        settings, "database_url", settings.database_url.rsplit("/", 1)[0] + "/" + name
    )
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    try:
        yield name, cfg
    finally:
        monkeypatch.undo()
        _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _version(db: str) -> str:
    return str(_query(db, "SELECT version_num FROM alembic_version")[0]["version_num"])


def _seed_base(db: str) -> dict[str, uuid.UUID]:
    ids = {k: uuid.uuid4() for k in ("epl", "burnley", "city", "arsenal", "spurs")}
    _execute(
        db,
        "INSERT INTO leagues (id, code, name, country) VALUES ($1, 'EPL', 'Premier League', "
        "'England')",
        ids["epl"],
    )
    for key, name in (
        ("burnley", "burnley"),
        ("city", "man city"),
        ("arsenal", "arsenal"),
        ("spurs", "tottenham"),
    ):
        _execute(
            db,
            "INSERT INTO teams (id, name, normalized_name, country) VALUES ($1, $2, $2, 'England')",
            ids[key],
            name,
        )
    return ids


def _fixture(
    db: str,
    ids: dict[str, uuid.UUID],
    home: str,
    away: str,
    *,
    season: str,
    kickoff: datetime,
    source: str,
    status: str = "finished",
) -> uuid.UUID:
    fid = uuid.uuid4()
    _execute(
        db,
        "INSERT INTO fixtures (id, league_id, season, home_team_id, away_team_id, kickoff_at, "
        "status, ft_home, ft_away, source) VALUES ($1, $2, $3, $4, $5, $6, $7, 1, 0, $8)",
        fid,
        ids["epl"],
        season,
        ids[home],
        ids[away],
        kickoff,
        status,
        source,
    )
    return fid


def _odds(db: str, fid: uuid.UUID, ts: datetime, outcome: str = "home", price: str = "2.0") -> None:
    _execute(
        db,
        "INSERT INTO odds (fixture_id, bookmaker, market, outcome, ts, price, is_closing) "
        "VALUES ($1, 'pinnacle', '1x2', $2, $3, $4::numeric, true)",
        fid,
        outcome,
        ts,
        price,
    )


def test_upgrade_canonicalises_seasons_fixes_kickoffs_and_links_refs(
    migdb: tuple[str, Config],
) -> None:
    db, cfg = migdb
    command.upgrade(cfg, "0013_model_weighting")
    ids = _seed_base(db)
    # Old conventions: live season "2026"; football-data UK wall-clock stored as UTC.
    live = _fixture(
        db,
        ids,
        "arsenal",
        "spurs",
        season="2026",
        kickoff=datetime(2026, 9, 20, 14, 0, tzinfo=UTC),
        source="api_football",
        status="live",
    )
    summer = _fixture(
        db,
        ids,
        "burnley",
        "city",
        season="2023-2024",
        kickoff=datetime(2023, 8, 11, 20, 0, tzinfo=UTC),
        source=FD,
    )
    winter = _fixture(
        db,
        ids,
        "city",
        "burnley",
        season="2023-2024",
        kickoff=datetime(2023, 12, 26, 15, 0, tzinfo=UTC),
        source=FD,
    )
    date_only = _fixture(
        db,
        ids,
        "arsenal",
        "city",
        season="2015-2016",
        kickoff=datetime(2015, 8, 8, 0, 0, tzinfo=UTC),
        source=FD,
    )
    for fid, ts in (
        (summer, datetime(2023, 8, 11, 20, 0, tzinfo=UTC)),
        (winter, datetime(2023, 12, 26, 15, 0, tzinfo=UTC)),
        (date_only, datetime(2015, 8, 8, 0, 0, tzinfo=UTC)),
    ):
        for outcome in ("home", "draw", "away"):
            _odds(db, fid, ts, outcome)

    command.upgrade(cfg, "head")

    assert _version(db) == "0015_identity_data"
    rows = {
        r["id"]: r
        for r in _query(db, "SELECT id, season, kickoff_at, kickoff_time_known FROM fixtures")
    }
    assert rows[live]["season"] == "2026-2027"
    assert rows[live]["kickoff_at"] == datetime(2026, 9, 20, 14, 0, tzinfo=UTC)  # untouched
    assert rows[summer]["kickoff_at"] == datetime(2023, 8, 11, 19, 0, tzinfo=UTC)  # BST
    assert rows[winter]["kickoff_at"] == datetime(2023, 12, 26, 15, 0, tzinfo=UTC)  # GMT
    assert rows[date_only]["kickoff_at"] == datetime(2015, 8, 8, 12, 0, tzinfo=UTC)
    assert rows[date_only]["kickoff_time_known"] is False
    assert rows[summer]["kickoff_time_known"] is True

    odds_ts = {(r["fixture_id"], r["ts"]) for r in _query(db, "SELECT fixture_id, ts FROM odds")}
    assert odds_ts == {
        (summer, datetime(2023, 8, 11, 19, 0, tzinfo=UTC)),
        (winter, datetime(2023, 12, 26, 15, 0, tzinfo=UTC)),
        (date_only, datetime(2015, 8, 8, 12, 0, tzinfo=UTC)),
    }
    assert len(_query(db, "SELECT 1 FROM odds")) == 9  # moved, not duplicated

    refs = {
        r["fixture_id"]: r["external_id"]
        for r in _query(
            db, "SELECT fixture_id, external_id FROM fixture_external_refs WHERE provider = $1", FD
        )
    }
    assert refs[summer] == "fd:E0:2023-2024:20230811:burnley:man city"
    assert refs[date_only] == "fd:E0:2015-2016:20150808:arsenal:man city"
    assert live not in refs
    assert _query(db, "SELECT season_start_month FROM leagues")[0]["season_start_month"] == 8


@pytest.mark.parametrize("conflict", ["season", "kickoff", "odds"])
def test_conflicts_stop_the_upgrade_and_change_nothing(
    migdb: tuple[str, Config], conflict: str
) -> None:
    db, cfg = migdb
    command.upgrade(cfg, "0013_model_weighting")
    ids = _seed_base(db)
    kickoff = datetime(2026, 9, 20, 14, 0, tzinfo=UTC)
    if conflict == "season":
        # The same match under "2026" and "2026-2027": relabelling collides.
        _fixture(db, ids, "arsenal", "spurs", season="2026", kickoff=kickoff, source="api_football")
        _fixture(db, ids, "arsenal", "spurs", season="2026-2027", kickoff=kickoff, source=FD)
    elif conflict == "kickoff":
        # football-data 20:00 (BST) becomes 19:00 UTC — where a row already is.
        _fixture(
            db,
            ids,
            "burnley",
            "city",
            season="2023-2024",
            kickoff=datetime(2023, 8, 11, 20, 0, tzinfo=UTC),
            source=FD,
        )
        _fixture(
            db,
            ids,
            "burnley",
            "city",
            season="2023-2024",
            kickoff=datetime(2023, 8, 11, 19, 0, tzinfo=UTC),
            source="api_football",
        )
    else:
        # A quote at the old kickoff and one already at the new kickoff.
        fid = _fixture(
            db,
            ids,
            "burnley",
            "city",
            season="2023-2024",
            kickoff=datetime(2023, 8, 11, 20, 0, tzinfo=UTC),
            source=FD,
        )
        _odds(db, fid, datetime(2023, 8, 11, 20, 0, tzinfo=UTC), price="2.0")
        _odds(db, fid, datetime(2023, 8, 11, 19, 0, tzinfo=UTC), price="2.1")
    before = (
        sorted(tuple(r) for r in _query(db, "SELECT id, season, kickoff_at FROM fixtures")),
        sorted(tuple(r) for r in _query(db, "SELECT fixture_id, ts, price FROM odds")),
    )

    with pytest.raises(RuntimeError, match="STOP"):
        command.upgrade(cfg, "head")

    assert _version(db) == "0013_model_weighting"  # 0014 rolled back too
    after = (
        sorted(tuple(r) for r in _query(db, "SELECT id, season, kickoff_at FROM fixtures")),
        sorted(tuple(r) for r in _query(db, "SELECT fixture_id, ts, price FROM odds")),
    )
    assert before == after
    columns = {
        r["column_name"]
        for r in _query(
            db, "SELECT column_name FROM information_schema.columns WHERE table_name = 'fixtures'"
        )
    }
    assert "kickoff_time_known" not in columns


def test_team_key_downgrade_stops_on_cross_country_names(migdb: tuple[str, Config]) -> None:
    db, cfg = migdb
    command.upgrade(cfg, "head")
    for country in ("Spain", "Argentina"):
        _execute(
            db,
            "INSERT INTO teams (id, name, normalized_name, country) "
            "VALUES ($1, 'Racing', 'racing', $2)",
            uuid.uuid4(),
            country,
        )
    with pytest.raises(RuntimeError, match="STOP"):
        command.downgrade(cfg, "0013_model_weighting")
    assert _version(db) == "0015_identity_data"


def test_round_trip_on_an_empty_database(migdb: tuple[str, Config]) -> None:
    db, cfg = migdb
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0013_model_weighting")
    assert _version(db) == "0013_model_weighting"
    command.upgrade(cfg, "head")
    assert _version(db) == "0015_identity_data"


def test_downgrade_stops_instead_of_dropping_a_conflicting_quote(
    migdb: tuple[str, Config],
) -> None:
    """A quote ingested after the upgrade at the timestamp a moved quote would
    return to: the downgrade must STOP, not silently lose either row."""
    db, cfg = migdb
    command.upgrade(cfg, "0013_model_weighting")
    ids = _seed_base(db)
    fid = _fixture(
        db,
        ids,
        "burnley",
        "city",
        season="2023-2024",
        kickoff=datetime(2023, 8, 11, 20, 0, tzinfo=UTC),
        source=FD,
    )
    _odds(db, fid, datetime(2023, 8, 11, 20, 0, tzinfo=UTC), price="2.0")
    command.upgrade(cfg, "head")
    # The closing quote now sits at 19:00 UTC; a later quote lands at 20:00 UTC.
    _odds(db, fid, datetime(2023, 8, 11, 20, 0, tzinfo=UTC), price="2.5")
    before = sorted(tuple(r) for r in _query(db, "SELECT fixture_id, ts, price FROM odds"))

    with pytest.raises(RuntimeError, match="STOP"):
        command.downgrade(cfg, "0013_model_weighting")

    assert _version(db) == "0015_identity_data"
    assert sorted(tuple(r) for r in _query(db, "SELECT fixture_id, ts, price FROM odds")) == before
