"""ER2-05: API sessions carry ``statement_timeout`` and ``lock_timeout``; worker,
CLI and Alembic sessions do not.

``create_app()`` turns the limits on for its own process before the first engine
is built (the test session imports ``app.main`` from ``conftest``, so it is an
API process). A worker is simulated by switching the process flag off and
rebuilding the engines.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import asyncpg
import pytest
from app.core import db
from app.core.config import get_settings
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

BACKEND = Path(__file__).resolve().parents[1]


async def _show(session: AsyncSession, name: str) -> str:
    return str((await session.execute(text(f"SHOW {name}"))).scalar_one())


def _asyncpg_dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def short_api_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sub-second limits so the cut-off tests run fast; engines rebuilt with them."""
    monkeypatch.setattr(db, "API_STATEMENT_TIMEOUT_MS", 500)
    monkeypatch.setattr(db, "API_LOCK_TIMEOUT_MS", 500)
    db.reset_engines()


@pytest.fixture
def worker_process(monkeypatch: pytest.MonkeyPatch, short_api_timeouts: None) -> None:
    """This process as a worker sees it: create_app() never ran."""
    monkeypatch.setattr(db, "_api_timeouts", False)
    db.reset_engines()


async def _sessions() -> AsyncIterator[tuple[str, AsyncSession]]:
    async with db._write_sessionmaker()() as s:
        yield "write", s
    async with db._read_sessionmaker()() as s:
        yield "read", s
    async with db.independent_transaction() as s:
        yield "security", s


async def test_api_engines_set_both_limits() -> None:
    async for name, session in _sessions():
        assert await _show(session, "statement_timeout") == "15s", name
        assert await _show(session, "lock_timeout") == "5s", name


async def test_api_session_is_cut_off_past_the_statement_timeout(
    short_api_timeouts: None,
) -> None:
    async with db._write_sessionmaker()() as session:
        with pytest.raises(DBAPIError) as exc_info:
            await session.execute(text("SELECT pg_sleep(1.5)"))
    assert "canceling statement due to statement timeout" in str(exc_info.value)


async def _with_users_table_locked(
    check: Callable[[], Awaitable[None]],
) -> None:
    """Hold ACCESS EXCLUSIVE on ``users`` from an independent connection while
    ``check`` runs."""
    holder = await asyncpg.connect(_asyncpg_dsn())
    try:
        tx = holder.transaction()
        await tx.start()
        await holder.execute("LOCK TABLE users IN ACCESS EXCLUSIVE MODE")
        try:
            await check()
        finally:
            await tx.rollback()
    finally:
        await holder.close()


async def test_api_session_gives_up_waiting_for_a_lock(
    monkeypatch: pytest.MonkeyPatch, short_api_timeouts: None
) -> None:
    # The statement limit well above the lock limit, so the lock wait is what ends it.
    monkeypatch.setattr(db, "API_STATEMENT_TIMEOUT_MS", 5_000)
    db.reset_engines()

    async def check() -> None:
        async with db._write_sessionmaker()() as session:
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(text("SELECT count(*) FROM users"))
        assert "canceling statement due to lock timeout" in str(exc_info.value)

    await _with_users_table_locked(check)


async def test_worker_session_is_not_cut_off(worker_process: None) -> None:
    async with db._write_sessionmaker()() as session:
        assert await _show(session, "statement_timeout") == "0"
        assert await _show(session, "lock_timeout") == "0"
        await session.execute(text("SELECT pg_sleep(1.5)"))


def test_enabling_after_an_engine_exists_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_api_timeouts", False)
    db.reset_engines()
    db._write_engine()
    with pytest.raises(RuntimeError, match="before the first engine"):
        db.enable_api_db_timeouts()


def test_create_app_is_the_only_switch() -> None:
    """Workers, the CLI and Alembic never import app.main, so they never call
    create_app(); a regression here would put API limits on long jobs."""
    probe = (
        "import sys\n"
        "import app.workers.arq_app, app.workers.tasks, app.workers.healthcheck, app.cli\n"
        "from app.core import db\n"
        "assert 'app.main' not in sys.modules, 'app.main imported'\n"
        "assert db._api_timeouts is False, 'timeouts on outside the API'\n"
        "print('ok')\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed argv, test-only
        [sys.executable, "-c", probe],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("ok")
    migrations_env = (BACKEND / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "app.main" not in migrations_env
    assert "_write_engine" not in migrations_env
