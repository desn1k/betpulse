"""The F5 guard: client requests that arrive as one of our own addresses.

When the API sees a client identity inside INTERNAL_NETWORK_CIDRS (the Docker
bridge gateway, or an internal hop it does not trust), every such client shares
one identity. The guard logs that once per process and records it in Redis, so
the admin system health page shows it to every API process's admin.
"""

from __future__ import annotations

import logging

import pytest
from app.core.config import get_settings
from app.core.redis import get_redis
from app.models.user import User, UserRole
from app.services import client_identity
from app.services.client_identity import (
    COLLAPSE_KEY,
    CollapseWatch,
    check_client_identity,
    read_record,
)
from httpx import AsyncClient
from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncSession

INTERNAL = "172.29.89.0/24,fd42:b7e1:5a29:89::/64"


@pytest.fixture(autouse=True)
def _fresh_watch(monkeypatch: MonkeyPatch) -> None:
    client_identity.reset_watch()
    # The migration tests run Alembic's env.py in this process, and its
    # fileConfig() disables every logger that already exists. The API process
    # never runs it (deploy.sh migrates in a one-off container).
    monkeypatch.setattr(client_identity.logger, "disabled", False)


async def test_first_sighting_is_logged_once_and_recorded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = get_redis()
    watch = CollapseWatch()
    with caplog.at_level(logging.WARNING, logger="app.services.client_identity"):
        await watch.observe(redis, "172.29.89.1", "/matches/1", now=1000.0)
        await watch.observe(redis, "172.29.89.1", "/matches/2", now=1001.0)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "172.29.89.1" in warnings[0].getMessage()
    assert "/matches/1" in warnings[0].getMessage()

    record = await read_record(redis)
    # The first sighting is written at once; the second waits for the next flush.
    assert record["count"] == "1"
    assert record["last_client"] == "172.29.89.1"
    assert record["last_path"] == "/matches/1"
    assert record["first_seen"] == record["last_seen"]
    assert 0 < await redis.ttl(COLLAPSE_KEY) <= client_identity.RECORD_TTL_SECONDS


async def test_sightings_are_flushed_at_most_once_a_minute() -> None:
    redis = get_redis()
    watch = CollapseWatch()
    await watch.observe(redis, "172.29.89.1", "/a", now=1000.0)
    first_seen = (await read_record(redis))["first_seen"]
    for n in range(5):
        await watch.observe(redis, "fd42:b7e1:5a29:89::1", f"/b{n}", now=1010.0 + n)
    assert (await read_record(redis))["count"] == "1"

    await watch.observe(redis, "fd42:b7e1:5a29:89::1", "/c", now=1000.0 + 60)
    record = await read_record(redis)
    assert record["count"] == "7"
    assert record["last_client"] == "fd42:b7e1:5a29:89::1"
    assert record["last_path"] == "/c"
    assert record["first_seen"] == first_seen


async def test_a_redis_failure_never_breaks_the_request(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenRedis:
        def pipeline(self, transaction: bool = True) -> object:
            raise ConnectionError("redis down")

    watch = CollapseWatch()
    with caplog.at_level(logging.WARNING, logger="app.services.client_identity"):
        await watch.observe(BrokenRedis(), "172.29.89.1", "/a", now=1000.0)  # type: ignore[arg-type]
    assert any("172.29.89.1" in r.getMessage() for r in caplog.records)


async def test_health_is_ok_without_sightings(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_network_cidrs", INTERNAL)
    component = await check_client_identity(get_redis(), settings)
    assert component.name == "client_ip"
    assert component.status == "ok"


async def test_health_is_degraded_after_a_sighting(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_network_cidrs", INTERNAL)
    await CollapseWatch().observe(get_redis(), "172.29.89.1", "/matches/9", now=1000.0)
    component = await check_client_identity(get_redis(), settings)
    assert component.status == "degraded"
    assert component.detail is not None
    assert "172.29.89.1" in component.detail
    assert "share one identity" in component.detail
    assert component.meta["count"] == 1
    assert component.meta["last_path"] == "/matches/9"


async def test_health_says_when_the_check_is_off(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_network_cidrs", None)
    component = await check_client_identity(get_redis(), settings)
    assert component.status == "not_configured"
    assert component.detail is not None
    assert "INTERNAL_NETWORK_CIDRS" in component.detail


# --- through the app ------------------------------------------------------------


async def test_requests_from_the_gateway_show_on_the_health_page(
    client: AsyncClient, session: AsyncSession, monkeypatch: MonkeyPatch
) -> None:
    from app.core.security import create_access_token

    monkeypatch.setattr(get_settings(), "internal_network_cidrs", INTERNAL)
    # The test client connects from 127.0.0.1, which the development default
    # of TRUSTED_PROXY_CIDRS trusts: it plays the BFF.
    assert (await client.get("/health", headers={"X-Forwarded-For": "198.51.100.7"})).status_code
    assert await get_redis().exists(COLLAPSE_KEY) == 0
    # No header: the proxy's own request, never a sighting.
    await client.get("/health")
    assert await get_redis().exists(COLLAPSE_KEY) == 0

    await client.get("/health", headers={"X-Forwarded-For": "172.29.89.1"})
    assert (await read_record(get_redis()))["last_client"] == "172.29.89.1"

    admin = User(
        email="f5-admin@example.com",
        password_hash="x",
        role=UserRole.admin,
        must_change_password=False,
        totp_enabled=True,
    )
    session.add(admin)
    await session.commit()
    token = create_access_token(subject=str(admin.id), role="admin")
    headers = {"Authorization": f"Bearer {token}"}
    body = (await client.get("/admin/system/health", headers=headers)).json()
    components = {c["name"]: c for c in body["components"]}
    assert components["client_ip"]["status"] == "degraded"
    assert body["status"] == "degraded"
