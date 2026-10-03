"""Migration 0017: the single-champion index. A database that already holds two
champions STOPS the upgrade with the list and is left unchanged; otherwise the
index makes a second champion impossible. Real Postgres via Alembic."""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from tests.migrations.test_identity_migrations import _execute, _query, _version, migdb

__all__ = ["migdb"]

HEAD = "0017_single_champion"
PREVIOUS = "0016_ingestion_resume"


def _registry_row(db: str, method: str, status: str) -> uuid.UUID:
    row_id = uuid.uuid4()
    _execute(
        db,
        "INSERT INTO model_registry (id, method, version, status, is_enabled, is_visible, "
        "display_weight, min_samples, sample_count) "
        "VALUES ($1, $2, 'v1', $3::model_status, true, true, 0, 300, 500)",
        row_id,
        method,
        status,
    )
    return row_id


def _index_exists(db: str) -> bool:
    return bool(
        _query(db, "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_model_registry_single_champion'")
    )


def test_two_champions_stop_the_upgrade_and_change_nothing(migdb: tuple[str, Config]) -> None:
    db, cfg = migdb
    command.upgrade(cfg, PREVIOUS)
    elo = _registry_row(db, "elo", "champion")
    dc = _registry_row(db, "dixon_coles", "champion")

    with pytest.raises(RuntimeError, match="STOP: 2 champion rows") as exc:
        command.upgrade(cfg, HEAD)
    assert str(elo) in str(exc.value) and str(dc) in str(exc.value)

    assert _version(db) == PREVIOUS
    assert not _index_exists(db)
    statuses = {
        r["method"]: r["status"] for r in _query(db, "SELECT method, status FROM model_registry")
    }
    assert statuses == {"elo": "champion", "dixon_coles": "champion"}


def test_index_rejects_a_second_champion_and_downgrade_drops_it(
    migdb: tuple[str, Config],
) -> None:
    db, cfg = migdb
    command.upgrade(cfg, PREVIOUS)
    _registry_row(db, "elo", "champion")
    command.upgrade(cfg, HEAD)
    assert _version(db) == HEAD and _index_exists(db)

    with pytest.raises(asyncpg.UniqueViolationError):
        _registry_row(db, "dixon_coles", "champion")
    _registry_row(db, "dixon_coles", "challenger")  # any number of challengers

    command.downgrade(cfg, PREVIOUS)
    assert not _index_exists(db)
    command.upgrade(cfg, HEAD)
    assert _index_exists(db)
