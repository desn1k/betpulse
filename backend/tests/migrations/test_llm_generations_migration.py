"""Migration 0018 (ER-H-02): the LLM analysis cache is keyed per language, and
every generation is kept in the append-only ``llm_generations`` journal.

Upgrade back-fills one journal row per cached analysis. Downgrade keeps the
newest analysis per ``(fixture_id, model)`` (``created_at DESC, id DESC``),
logs how many analysis rows it deleted and how many journal rows it drops,
then drops the journal and restores the old constraint. Real Postgres via
Alembic.
"""

from __future__ import annotations

import logging
import logging.config
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from tests.migrations.test_identity_migrations import (
    _execute,
    _fixture,
    _query,
    _seed_base,
    _version,
    migdb,
)

__all__ = ["migdb"]

HEAD = "0018_llm_generations"
PREVIOUS = "0017_single_champion"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _keep_caplog(monkeypatch: pytest.MonkeyPatch) -> None:
    # env.py applies alembic.ini's logging config, which replaces the root
    # handlers (pytest's capture handler among them).
    monkeypatch.setattr(logging.config, "fileConfig", lambda *a, **k: None)


def _analysis(
    db: str,
    fixture_id: uuid.UUID,
    *,
    language: str,
    created_at: datetime,
    content: str,
    row_id: uuid.UUID | None = None,
) -> uuid.UUID:
    row_id = row_id or uuid.uuid4()
    _execute(
        db,
        "INSERT INTO llm_analyses (id, fixture_id, provider, model, language, content, "
        "tokens_in, tokens_out, cost, created_at) "
        "VALUES ($1, $2, 'test', 'm', $3, $4, 100, 50, 0.125, $5)",
        row_id,
        fixture_id,
        language,
        content,
        created_at,
    )
    return row_id


def _journal(db: str) -> list[asyncpg.Record]:
    return _query(
        db,
        "SELECT fixture_id, model, language, tokens_in, tokens_out, cost, created_at "
        "FROM llm_generations ORDER BY created_at, language",
    )


def _constraints(db: str) -> set[str]:
    rows = _query(
        db,
        "SELECT conname FROM pg_constraint WHERE conrelid = 'llm_analyses'::regclass "
        "AND contype = 'u'",
    )
    return {r["conname"] for r in rows}


def _table_exists(db: str, name: str) -> bool:
    return bool(_query(db, "SELECT 1 FROM information_schema.tables WHERE table_name = $1", name))


def _two_fixtures(db: str) -> tuple[uuid.UUID, uuid.UUID]:
    ids = _seed_base(db)
    kickoff = T0 + timedelta(days=1)
    first = _fixture(db, ids, "burnley", "city", season="2026-2027", kickoff=kickoff, source="t")
    second = _fixture(db, ids, "arsenal", "spurs", season="2026-2027", kickoff=kickoff, source="t")
    return first, second


def test_upgrade_backfills_the_journal_and_keys_the_cache_per_language(
    migdb: tuple[str, Config],
) -> None:
    db, cfg = migdb
    command.upgrade(cfg, PREVIOUS)
    first, second = _two_fixtures(db)
    _analysis(db, first, language="en", created_at=T0, content="first en")
    _analysis(db, second, language="ru", created_at=T0 + timedelta(hours=1), content="second ru")

    command.upgrade(cfg, HEAD)

    assert _version(db) == HEAD
    journal = _journal(db)
    assert [(r["fixture_id"], r["language"]) for r in journal] == [(first, "en"), (second, "ru")]
    assert all(
        (r["model"], r["tokens_in"], r["tokens_out"], str(r["cost"])) == ("m", 100, 50, "0.125000")
        for r in journal
    )
    assert journal[0]["created_at"] == T0
    assert _constraints(db) == {"uq_llm_analysis_fixture_model_language"}
    # A second language for the same fixture and model is now allowed …
    _analysis(db, first, language="ru", created_at=T0 + timedelta(hours=2), content="first ru")
    # … a second row for the same language is not.
    with pytest.raises(asyncpg.UniqueViolationError):
        _analysis(db, first, language="en", created_at=T0 + timedelta(hours=3), content="dup")


def test_downgrade_keeps_the_newest_logs_both_counts_and_reupgrade_does_not_duplicate(
    migdb: tuple[str, Config], caplog: pytest.LogCaptureFixture
) -> None:
    db, cfg = migdb
    command.upgrade(cfg, PREVIOUS)
    first, second = _two_fixtures(db)
    _analysis(db, first, language="en", created_at=T0, content="first en")
    _analysis(db, second, language="en", created_at=T0, content="second en")
    command.upgrade(cfg, HEAD)
    # After 0018: a newer generation of the first fixture in Russian, journaled.
    _analysis(db, first, language="ru", created_at=T0 + timedelta(hours=1), content="first ru")
    _execute(
        db,
        "INSERT INTO llm_generations (id, fixture_id, model, language, tokens_in, tokens_out, "
        "cost, created_at) VALUES ($1, $2, 'm', 'ru', 100, 50, 0.125, $3)",
        uuid.uuid4(),
        first,
        T0 + timedelta(hours=1),
    )
    assert len(_journal(db)) == 3

    caplog.set_level(logging.INFO, logger="alembic")
    command.downgrade(cfg, PREVIOUS)

    assert _version(db) == PREVIOUS
    kept = {
        r["fixture_id"]: r["content"]
        for r in _query(db, "SELECT fixture_id, content FROM llm_analyses")
    }
    assert kept == {first: "first ru", second: "second en"}
    assert _constraints(db) == {"uq_llm_analysis_fixture_model"}
    assert not _table_exists(db, "llm_generations")
    messages = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("alembic"))
    assert "1 llm_analyses rows deleted" in messages
    assert "3 llm_generations rows dropped" in messages

    command.upgrade(cfg, HEAD)
    journal = _journal(db)
    assert sorted((r["fixture_id"], r["language"]) for r in journal) == sorted(
        [(first, "ru"), (second, "en")]
    )


def test_downgrade_breaks_a_created_at_tie_by_id(migdb: tuple[str, Config]) -> None:
    db, cfg = migdb
    command.upgrade(cfg, PREVIOUS)
    first, _ = _two_fixtures(db)
    command.upgrade(cfg, HEAD)
    low, high = sorted([uuid.uuid4(), uuid.uuid4()])
    _analysis(db, first, language="en", created_at=T0, content="low id", row_id=low)
    _analysis(db, first, language="ru", created_at=T0, content="high id", row_id=high)

    command.downgrade(cfg, PREVIOUS)

    assert [r["content"] for r in _query(db, "SELECT content FROM llm_analyses")] == ["high id"]


def test_round_trip_on_an_empty_database(migdb: tuple[str, Config]) -> None:
    db, cfg = migdb
    command.upgrade(cfg, HEAD)
    assert _journal(db) == []
    command.downgrade(cfg, PREVIOUS)
    assert _constraints(db) == {"uq_llm_analysis_fixture_model"}
    command.upgrade(cfg, HEAD)
    assert _version(db) == HEAD
