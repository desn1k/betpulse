"""Integration tests for the auth API (register, login, refresh, 2FA, RBAC)."""

from __future__ import annotations

import uuid
from typing import Any

import pyotp
import pytest
from app.core.config import get_settings
from app.models.user import User, UserRole
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

PASSWORD = "correct horse battery staple"


def _email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    r = await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    data: dict[str, Any] = r.json()
    return data


@pytest.mark.asyncio
async def test_register_login_me(client: AsyncClient) -> None:
    email = _email()
    body = await _register_and_login(client, email)
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == email
    assert body["user"]["role"] == "user"

    settings = get_settings()
    assert settings.refresh_cookie_name in client.cookies
    assert settings.csrf_cookie_name in client.cookies

    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert r.status_code == 200
    assert r.json()["email"] == email


@pytest.mark.asyncio
async def test_me_requires_valid_token(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401
    r = await client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_login_wrong_password_is_401(client: AsyncClient) -> None:
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    r = await client.post("/auth/login", json={"email": email, "password": "wrong-pass-xx"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_refresh_requires_csrf_and_rotates(client: AsyncClient) -> None:
    await _register_and_login(client, _email())
    settings = get_settings()
    csrf = client.cookies[settings.csrf_cookie_name]

    # Missing CSRF header → rejected.
    assert (await client.post("/auth/refresh")).status_code == 403

    # With the double-submit header → rotates and returns a new access token.
    r = await client.post("/auth/refresh", headers={settings.csrf_header_name: csrf})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]


@pytest.mark.asyncio
async def test_logout_invalidates_refresh(client: AsyncClient) -> None:
    await _register_and_login(client, _email())
    settings = get_settings()
    csrf = client.cookies[settings.csrf_cookie_name]

    r = await client.post("/auth/logout", headers={settings.csrf_header_name: csrf})
    assert r.status_code == 200

    # Cookie was cleared, so refresh no longer works.
    r = await client.post("/auth/refresh", headers={settings.csrf_header_name: csrf})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_per_ip_rate_limit_returns_429(client: AsyncClient) -> None:
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    settings = get_settings()

    last_status = 200
    for _ in range(settings.rate_limit_login_per_minute + 1):
        r = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
        last_status = r.status_code
    assert last_status == 429


@pytest.mark.asyncio
async def test_two_factor_enable_then_required_on_login(
    client: AsyncClient, session: AsyncSession
) -> None:
    email = _email()
    body = await _register_and_login(client, email)
    auth = {"Authorization": f"Bearer {body['access_token']}"}

    setup = await client.post("/auth/2fa/setup", headers=auth)
    assert setup.status_code == 200
    secret = setup.json()["secret"]

    code = pyotp.TOTP(secret).now()
    enable = await client.post("/auth/2fa/enable", headers=auth, json={"code": code})
    assert enable.status_code == 200

    # Login now demands a TOTP code.
    r = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 401
    assert r.headers.get("X-2FA-Required") == "true"

    r = await client.post(
        "/auth/login",
        json={"email": email, "password": PASSWORD, "totp_code": pyotp.TOTP(secret).now()},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_two_factor_disable(client: AsyncClient) -> None:
    email = _email()
    body = await _register_and_login(client, email)
    auth = {"Authorization": f"Bearer {body['access_token']}"}

    secret = (await client.post("/auth/2fa/setup", headers=auth)).json()["secret"]
    await client.post("/auth/2fa/enable", headers=auth, json={"code": pyotp.TOTP(secret).now()})

    # Wrong code cannot disable 2FA.
    bad = await client.post("/auth/2fa/disable", headers=auth, json={"code": "000000"})
    assert bad.status_code == 400

    ok = await client.post(
        "/auth/2fa/disable", headers=auth, json={"code": pyotp.TOTP(secret).now()}
    )
    assert ok.status_code == 200

    # Login no longer requires a code.
    r = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_gate_blocks_non_admin_and_ungated_admin(
    client: AsyncClient, session: AsyncSession
) -> None:
    # Regular user → 403 on admin route.
    email = _email()
    body = await _register_and_login(client, email)
    auth = {"Authorization": f"Bearer {body['access_token']}"}
    assert (await client.get("/admin/ping", headers=auth)).status_code == 403

    # Promote to admin but leave must_change_password / no 2FA → still blocked.
    await session.execute(update(User).where(User.email == email).values(role=UserRole.admin))
    await session.commit()
    # New token carrying the admin role.
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    admin_auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.get("/admin/ping", headers=admin_auth)
    assert r.status_code == 403  # 2FA not enabled yet
