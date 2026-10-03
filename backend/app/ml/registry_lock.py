"""Registry write lock (model governance).

Every change of champion status or consensus weights runs under one Postgres
advisory lock, held until the transaction ends (``pg_advisory_xact_lock``):

* the nightly re-evaluation **tries** the lock and skips the run if it is busy
  (:func:`try_lock_registry`) — the Redis lock in the ARQ task stays as the
  first line, this one also covers a second worker or a manual run;
* admin actions **wait** for it, but at most ``registry_lock_timeout_ms``
  (:func:`lock_registry`); past that they fail with :class:`RegistryBusy`
  instead of hanging the request.

The lock is re-entrant within a transaction, so a service that takes it may be
called by a route that already holds it. The two-int form keeps it apart from
the single-bigint keys used elsewhere (refresh-token families).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

# (namespace, key) for pg_advisory_xact_lock(int, int).
REGISTRY_LOCK_NAMESPACE = 0x4250  # "BP"
REGISTRY_LOCK_KEY = 1
LOCK_NOT_AVAILABLE = "55P03"  # SQLSTATE raised when lock_timeout expires


class RegistryBusy(Exception):
    """Another transaction holds the registry lock past the admin timeout."""


async def try_lock_registry(session: AsyncSession) -> bool:
    """Take the registry lock if it is free; never waits."""
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:ns, :key)"),
        {"ns": REGISTRY_LOCK_NAMESPACE, "key": REGISTRY_LOCK_KEY},
    )
    return bool(acquired)


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


async def lock_registry(session: AsyncSession, *, timeout_ms: int | None = None) -> None:
    """Wait for the registry lock, at most ``timeout_ms`` (default from settings).

    Raises :class:`RegistryBusy` when the wait times out; the transaction is
    then aborted and must be rolled back by the caller."""
    timeout = get_settings().registry_lock_timeout_ms if timeout_ms is None else timeout_ms
    previous = await session.scalar(text("SELECT current_setting('lock_timeout')"))
    await session.execute(
        text("SELECT set_config('lock_timeout', :v, true)"), {"v": f"{int(timeout)}ms"}
    )
    try:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": REGISTRY_LOCK_NAMESPACE, "key": REGISTRY_LOCK_KEY},
        )
    except DBAPIError as exc:
        if _sqlstate(exc) == LOCK_NOT_AVAILABLE:
            raise RegistryBusy from exc
        raise
    # The timeout is for this wait only, not for the rest of the transaction.
    await session.execute(text("SELECT set_config('lock_timeout', :v, true)"), {"v": str(previous)})
