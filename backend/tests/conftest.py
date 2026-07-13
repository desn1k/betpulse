"""Shared test fixtures.

Environment defaults are set **before** importing any app module so that the
cached ``Settings`` picks them up. Integration tests run against real Postgres
and Redis (provided by CI service containers, or locally via docker).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "0" * 64)
os.environ.setdefault("DATA_ENCRYPTION_KEY", "1" * 64)
os.environ.setdefault("AUTH_COOKIE_SECURE", "false")
os.environ.setdefault("ADMIN_2FA_REQUIRED", "true")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://football:football@localhost:5432/football"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from collections.abc import AsyncIterator  # noqa: E402

import app.models  # noqa: E402,F401  (register models on metadata)
import pytest_asyncio  # noqa: E402
from app.core.db import Base, _write_engine, _write_sessionmaker, reset_engines  # noqa: E402
from app.core.redis import get_redis, reset_redis  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

_TABLES = "users, refresh_tokens, email_verification_tokens, audit_log"


@pytest_asyncio.fixture(autouse=True)
async def _db_and_redis() -> AsyncIterator[None]:
    # Rebuild cached engines/clients so they bind to the current event loop.
    reset_engines()
    reset_redis()
    engine = _write_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    redis = get_redis()
    await redis.flushdb()
    yield
    await redis.aclose()
    await engine.dispose()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with _write_sessionmaker()() as s:
        yield s
        await s.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
