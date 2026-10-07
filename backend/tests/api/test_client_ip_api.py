"""End-to-end client IP behaviour: quotas, rate limits and audit entries.

The default test client connects from 127.0.0.1, which the development default
of TRUSTED_PROXY_CIDRS trusts — it plays the Next.js BFF. ``_untrusted_client``
connects from a public address, like a request that bypassed the proxies.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.fixture import Fixture, FixtureStatus
from app.models.reference import League, Team
from app.services.audit import AuditAction
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

PASSWORD = "correct horse battery staple"
GUEST_DAILY_VIEWS = 3


@asynccontextmanager
async def _untrusted_client(peer: str = "192.0.2.50") -> AsyncIterator[AsyncClient]:
    from app.main import app

    transport = ASGITransport(app=app, client=(peer, 40000))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _match_ids(session: AsyncSession, count: int) -> list[uuid.UUID]:
    """Committed fixtures (no predictions): enough for match detail views. The
    quota counts distinct matches (F6), so each view below uses a new one."""
    league = League(code="EPL", name="EPL League")
    session.add(league)
    await session.flush()
    fixtures = []
    for _ in range(count):
        home = Team(name="Arsenal", normalized_name=f"arsenal-{uuid.uuid4().hex[:6]}")
        away = Team(name="Chelsea", normalized_name=f"chelsea-{uuid.uuid4().hex[:6]}")
        session.add_all([home, away])
        await session.flush()
        fixture = Fixture(
            league_id=league.id,
            season="2025-2026",
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=datetime.now(UTC) + timedelta(hours=6),
            status=FixtureStatus.scheduled,
        )
        session.add(fixture)
        fixtures.append(fixture)
    await session.commit()
    return [f.id for f in fixtures]


async def _view_match(client: AsyncClient, forwarded_for: str, match_id: uuid.UUID) -> int:
    # A real match: 200 while the guest budget remains, 403 once it is spent (an
    # unknown id answers 404 without spending it, ER-M-05).
    response = await client.get(f"/matches/{match_id}", headers={"X-Forwarded-For": forwarded_for})
    return response.status_code


async def _register(client: AsyncClient) -> str:
    email = f"ip-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return email


async def _login(client: AsyncClient, email: str, forwarded_for: str) -> int:
    response = await client.post(
        "/auth/login",
        json={"email": email, "password": PASSWORD},
        headers={"X-Forwarded-For": forwarded_for},
    )
    return response.status_code


# --- guest daily quota ----------------------------------------------------------


async def test_guests_behind_the_bff_get_separate_daily_quotas(
    client: AsyncClient, session: AsyncSession
) -> None:
    *viewed, new = await _match_ids(session, GUEST_DAILY_VIEWS + 1)
    for match_id in viewed:
        assert await _view_match(client, "198.51.100.1", match_id) == 200
    assert await _view_match(client, "198.51.100.1", new) == 403

    assert await _view_match(client, "198.51.100.2", new) == 200
    listing = await client.get("/matches", headers={"X-Forwarded-For": "198.51.100.2"})
    assert listing.json()["matches_remaining"] == GUEST_DAILY_VIEWS - 1


async def test_spoofed_forwarded_for_from_untrusted_peer_is_ignored(session: AsyncSession) -> None:
    *viewed, new = await _match_ids(session, GUEST_DAILY_VIEWS + 1)
    async with _untrusted_client() as untrusted:
        for n, match_id in enumerate(viewed):
            assert await _view_match(untrusted, f"198.51.100.{10 + n}", match_id) == 200
        # Rotating the header does not buy a fresh quota: the peer is the identity.
        assert await _view_match(untrusted, "198.51.100.99", new) == 403


async def test_guest_quota_buckets_ipv6_by_64(client: AsyncClient, session: AsyncSession) -> None:
    *viewed, new = await _match_ids(session, GUEST_DAILY_VIEWS + 1)
    for n, match_id in enumerate(viewed):
        assert await _view_match(client, f"2001:db8:aa:1::{n + 1}", match_id) == 200
    assert await _view_match(client, "2001:db8:aa:1:ffff::1", new) == 403
    assert await _view_match(client, "2001:db8:aa:2::1", new) == 200


# --- login rate limit -----------------------------------------------------------


async def test_login_limit_is_per_client_ip_not_global(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_login_per_minute", 1)
    email = await _register(client)

    assert await _login(client, email, "198.51.100.20") == 200
    assert await _login(client, email, "198.51.100.20") == 429
    # Another user behind the same BFF is not locked out.
    assert await _login(client, email, "198.51.100.21") == 200


async def test_login_limit_cannot_be_dodged_with_spoofed_header(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_login_per_minute", 1)
    email = await _register(client)

    async with _untrusted_client() as untrusted:
        assert await _login(untrusted, email, "198.51.100.30") == 200
        assert await _login(untrusted, email, "198.51.100.31") == 429


async def test_login_limit_buckets_ipv6_by_64(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_login_per_minute", 1)
    email = await _register(client)

    assert await _login(client, email, "2001:db8:bb:1::1") == 200
    assert await _login(client, email, "2001:db8:bb:1::2") == 429
    assert await _login(client, email, "2001:db8:bb:2::1") == 200


# --- audit log ------------------------------------------------------------------


async def _login_audit_ips(session: AsyncSession) -> list[str | None]:
    rows = await session.execute(
        select(AuditLog.ip).where(AuditLog.action == AuditAction.LOGIN_SUCCESS)
    )
    return list(rows.scalars())


async def test_audit_log_stores_the_full_real_client_ip(
    client: AsyncClient, session: AsyncSession
) -> None:
    email = await _register(client)
    assert await _login(client, email, "2001:db8:cc:1::abcd") == 200
    assert await _login_audit_ips(session) == ["2001:db8:cc:1::abcd"]


async def test_invalid_forwarded_for_falls_back_to_the_peer(
    client: AsyncClient, session: AsyncSession
) -> None:
    email = await _register(client)
    assert await _login(client, email, "not-an-ip") == 200
    assert await _login_audit_ips(session) == ["127.0.0.1"]


# --- admin mutation limit -------------------------------------------------------


async def test_admin_mutation_limit_ignores_spoofed_header_from_untrusted_peer(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_admin_mutation_per_minute", 1)
    body = {"name": "x", "roles": []}

    async with _untrusted_client() as untrusted:
        first = await untrusted.post(
            "/admin/providers", json=body, headers={"X-Forwarded-For": "198.51.100.40"}
        )
        rotated = await untrusted.post(
            "/admin/providers", json=body, headers={"X-Forwarded-For": "198.51.100.41"}
        )

    assert first.status_code == 401
    assert rotated.status_code == 429
