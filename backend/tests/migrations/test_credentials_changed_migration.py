"""Migration 0019 (ER2-01): ``users.credentials_changed_at``.

Upgrade adds the column as NULL for every existing user (nothing to revoke, so
the deploy signs nobody out); downgrade drops it and keeps the users; a second
upgrade adds it again. Real Postgres via Alembic, on a database with users.
"""

from __future__ import annotations

import uuid

from alembic import command
from alembic.config import Config
from tests.migrations.test_identity_migrations import _execute, _query, _version, migdb

__all__ = ["migdb"]

HEAD = "0019_credentials_changed_at"
PREVIOUS = "0018_llm_generations"


def _add_user(db: str, email: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    _execute(
        db,
        "INSERT INTO users (id, email, password_hash, role, tier, is_active, is_verified, "
        "must_change_password, totp_enabled, failed_login_count) "
        "VALUES ($1, $2, 'hash', 'user', 'free', true, false, false, false, 0)",
        user_id,
        email,
    )
    return user_id


def _has_column(db: str) -> bool:
    return bool(
        _query(
            db,
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'credentials_changed_at'",
        )
    )


def test_round_trip_on_a_database_with_users(migdb: tuple[str, Config]) -> None:
    db, cfg = migdb
    command.upgrade(cfg, PREVIOUS)
    first = _add_user(db, "one@example.com")
    second = _add_user(db, "two@example.com")

    command.upgrade(cfg, HEAD)
    assert _version(db) == HEAD
    rows = _query(db, "SELECT id, credentials_changed_at FROM users ORDER BY email")
    assert [(r["id"], r["credentials_changed_at"]) for r in rows] == [(first, None), (second, None)]

    _execute(db, "UPDATE users SET credentials_changed_at = now() WHERE id = $1", first)
    command.downgrade(cfg, PREVIOUS)
    assert _version(db) == PREVIOUS
    assert not _has_column(db)
    assert {r["id"] for r in _query(db, "SELECT id FROM users")} == {first, second}

    command.upgrade(cfg, HEAD)
    assert _has_column(db)
    rows = _query(db, "SELECT credentials_changed_at FROM users")
    assert all(r["credentials_changed_at"] is None for r in rows)
