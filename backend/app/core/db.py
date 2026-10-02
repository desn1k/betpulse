"""Async database engine and session factory.

A single session factory fronts a primary (read/write) engine and an optional
read-replica engine. Today both point at the same database; when a replica is
configured (``DATABASE_READ_URL``) read-only sessions transparently route to it
without any call-site change (see §18 horizontal-scaling seam).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


@lru_cache
def _write_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache
def _read_engine() -> AsyncEngine:
    settings = get_settings()
    if not settings.database_read_url:
        return _write_engine()
    return create_async_engine(
        settings.read_database_url,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache
def _write_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(_write_engine(), expire_on_commit=False, autoflush=False)


@lru_cache
def _read_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(_read_engine(), expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a read/write session (commit/rollback managed)."""
    async with _write_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def independent_transaction() -> AsyncIterator[AsyncSession]:
    """A short read/write transaction of its own, committed when the block exits
    normally (rolled back if it raises).

    It is independent of the request transaction from :func:`get_session`, so
    what it writes persists even when the request later ends in an error and is
    rolled back — and none of the request session's pending changes leak into
    it. Used for security state (failed-login counters, lockouts, token-family
    revocation, their audit rows) that must survive a 401/429. The caller must
    not hold uncommitted writes on rows this block updates, or it waits on its
    own lock.
    """
    async with _write_sessionmaker()() as session, session.begin():
        yield session


async def get_read_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a read-only session (routes to the replica)."""
    async with _read_sessionmaker()() as session:
        yield session


def reset_engines() -> None:
    """Clear cached engines/sessionmakers (used by tests after env changes)."""
    for cached in (
        _write_engine,
        _read_engine,
        _write_sessionmaker,
        _read_sessionmaker,
    ):
        cached.cache_clear()
