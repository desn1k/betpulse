"""Postgres NOTICE / WARNING messages raised by a migration reach the operator.

asyncpg drops server messages unless a log listener is registered, so a
migration's ``RAISE NOTICE`` printed nothing during ``alembic upgrade`` /
``downgrade``. ``migrations/env.py`` forwards them to the ``alembic`` logger,
which ``alembic.ini`` prints to stderr at INFO.

The forwarding is checked with a throwaway script directory that uses the real
``env.py`` and one revision raising both kinds of message. The offline
(``--sql``) mode and an upgrade of an empty database with the real migrations
must behave as before.
"""

from __future__ import annotations

import io
import logging
import logging.config
import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from tests.migrations.test_identity_migrations import BACKEND, _query, _version, migdb

__all__ = ["migdb"]

_REVISION = '''"""probe: raise a notice and a warning"""

from alembic import op

revision = "probe_messages"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN RAISE NOTICE 'probe notice %', 7; "
        "RAISE WARNING 'probe warning %', 8; END $$"
    )


def downgrade() -> None:
    pass
'''


@pytest.fixture
def probe_cfg(tmp_path: Path, migdb: tuple[str, Config]) -> Config:
    """An Alembic config on the real env.py with one probing revision."""
    _, cfg = migdb
    scripts = tmp_path / "migrations"
    (scripts / "versions").mkdir(parents=True)
    shutil.copy(BACKEND / "migrations" / "env.py", scripts / "env.py")
    (scripts / "versions" / "probe_messages.py").write_text(_REVISION, encoding="utf-8")
    cfg.set_main_option("script_location", str(scripts))
    return cfg


@pytest.fixture(autouse=True)
def _keep_caplog(monkeypatch: pytest.MonkeyPatch) -> None:
    # env.py applies alembic.ini's logging config, which replaces the root
    # handlers (pytest's capture handler among them).
    monkeypatch.setattr(logging.config, "fileConfig", lambda *a, **k: None)


def test_server_notice_and_warning_are_logged(
    probe_cfg: Config, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="alembic")
    command.upgrade(probe_cfg, "head")
    forwarded = [
        (r.levelno, r.getMessage()) for r in caplog.records if r.name.startswith("alembic")
    ]
    assert any(level == logging.INFO and "probe notice 7" in m for level, m in forwarded)
    assert any(level == logging.WARNING and "probe warning 8" in m for level, m in forwarded)


def test_offline_mode_is_unchanged(probe_cfg: Config, caplog: pytest.LogCaptureFixture) -> None:
    # --sql renders the statements without connecting: nothing runs, so nothing
    # is raised or forwarded, and the DO block is printed as written.
    caplog.set_level(logging.INFO, logger="alembic")
    buffer = io.StringIO()
    probe_cfg.output_buffer = buffer
    command.upgrade(probe_cfg, "head", sql=True)
    assert "RAISE NOTICE 'probe notice %', 7" in buffer.getvalue()
    assert not any("probe notice 7" in r.getMessage() for r in caplog.records)


def test_empty_database_upgrades_to_head(migdb: tuple[str, Config]) -> None:
    db, cfg = migdb
    command.upgrade(cfg, "head")
    assert _version(db) == ScriptDirectory.from_config(cfg).get_current_head()
    assert _query(db, "SELECT 1 FROM information_schema.tables WHERE table_name = 'fixtures'")
