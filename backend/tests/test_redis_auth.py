"""Redis ``requirepass``: every client authenticates, and production refuses to
start without a usable password.

The integration tests turn ``requirepass`` on for the test Redis and restore it
in ``finally``: connections already open stay authenticated, new ones must send
the password. Each client is built the way its process builds it (the API's
client, the API's ARQ pool, the ARQ worker settings, the worker healthcheck).
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from app.core.arq import get_arq_pool
from app.core.config import Settings, get_settings
from app.core.redis import get_redis, reset_redis
from app.workers import healthcheck
from app.workers.queues import Queue
from arq.connections import ArqRedis
from arq.constants import health_check_key_suffix
from redis.asyncio import Redis
from redis.exceptions import AuthenticationError, ResponseError

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = secrets.token_hex(32)


def _prod(**overrides: str) -> Settings:
    values = {
        "environment": "production",
        "secret_key": secrets.token_hex(32),
        "data_encryption_key": secrets.token_hex(32),
        "trusted_proxy_cidrs": "172.29.89.10/32",
        "internal_network_cidrs": "172.29.89.0/24",
        "cors_allowed_origins": "https://app.example.test",
        "redis_url": "redis://redis:6379/0",
        "redis_password": PASSWORD,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


# --- configuration -------------------------------------------------------------


def test_redis_dsn_carries_the_password() -> None:
    settings = Settings(redis_url="redis://redis:6379/0", redis_password=PASSWORD)
    assert settings.redis_dsn == f"redis://:{PASSWORD}@redis:6379/0"


def test_redis_dsn_keeps_a_password_already_in_the_url() -> None:
    url = f"redis://:{PASSWORD}@redis:6379/0"
    assert Settings(redis_url=url).redis_dsn == url
    assert Settings(redis_url=url, redis_password=PASSWORD).redis_dsn == url


def test_a_url_password_that_differs_from_redis_password_is_refused() -> None:
    with pytest.raises(ValueError, match="REDIS_URL and REDIS_PASSWORD"):
        Settings(redis_url=f"redis://:{PASSWORD}@redis:6379/0", redis_password="other" * 8)


def test_development_runs_without_a_password() -> None:
    assert Settings(redis_url="redis://localhost:6379/0").redis_dsn == "redis://localhost:6379/0"


def test_production_accepts_a_random_hex_password() -> None:
    assert PASSWORD in _prod().redis_dsn


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("", "REDIS_PASSWORD"),
        ("changeme", "REDIS_PASSWORD"),
        (secrets.token_hex(8), "REDIS_PASSWORD"),
        # Rendered into REDIS_URL by Compose: must not need percent-encoding.
        (secrets.token_urlsafe(24) + "@/:", "letters and digits"),
    ],
    ids=["empty", "placeholder", "short", "needs-url-encoding"],
)
def test_production_refuses_an_unusable_password(password: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason) as exc:
        _prod(redis_password=password)
    if password:
        assert password not in str(exc.value)


# --- every client against a Redis that requires the password -------------------


@pytest_asyncio.fixture
async def redis_requires_password(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    settings = get_settings()
    admin = Redis.from_url(settings.redis_url)
    await admin.config_set("requirepass", PASSWORD)
    try:
        monkeypatch.setattr(settings, "redis_password", PASSWORD)
        reset_redis()
        yield PASSWORD
    finally:
        await admin.config_set("requirepass", "")
        await admin.aclose()
        reset_redis()


async def test_requirepass_is_really_on(redis_requires_password: str) -> None:
    anonymous = Redis.from_url(get_settings().redis_url)
    try:
        with pytest.raises((AuthenticationError, ResponseError)):
            await anonymous.ping()
    finally:
        await anonymous.aclose()


async def test_api_client_authenticates(redis_requires_password: str) -> None:
    client = get_redis()
    try:
        assert await client.ping()
    finally:
        await client.aclose()


async def test_api_arq_pool_authenticates(redis_requires_password: str) -> None:
    pools = cast(AsyncGenerator[ArqRedis, None], get_arq_pool())
    pool = await anext(pools)
    try:
        assert await pool.ping()
    finally:
        await pools.aclose()


async def test_worker_healthcheck_authenticates(redis_requires_password: str) -> None:
    client = get_redis()
    try:
        await client.set(Queue.REALTIME + health_check_key_suffix, "ok", ex=60)
    finally:
        await client.aclose()
    assert healthcheck.main(["realtime"]) == 0


def test_worker_settings_carry_the_password() -> None:
    """ARQ workers build RedisSettings at import, from the environment."""
    probe = "from app.workers.arq_app import REDIS_SETTINGS\nprint(REDIS_SETTINGS.password)\n"
    env = {**os.environ, "REDIS_URL": "redis://redis:6379/0", "REDIS_PASSWORD": PASSWORD}
    result = subprocess.run(  # noqa: S603 - fixed argv, test-only
        [sys.executable, "-c", probe],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == PASSWORD
