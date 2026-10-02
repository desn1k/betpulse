"""A rejected refresh (401) clears the auth cookies (refresh + CSRF)."""

from __future__ import annotations

import uuid

import pytest
from app.core.config import get_settings
from app.core.db import _write_sessionmaker
from app.models.refresh_token import RefreshToken
from httpx import AsyncClient, Response
from sqlalchemy import func, text, update

PASSWORD = "correct horse battery staple"


def _cleared(response: Response) -> dict[str, str]:
    """Cookie name → its Set-Cookie line, for cookies the response deletes."""
    out = {}
    for line in response.headers.get_list("set-cookie"):
        name = line.split("=", 1)[0]
        lowered = line.lower()
        if "max-age=0" in lowered or "expires=thu, 01 jan 1970" in lowered:
            out[name] = line
    return out


async def _refresh(client: AsyncClient, refresh_token: str, csrf: str) -> Response:
    settings = get_settings()
    client.cookies.clear()
    client.cookies.set(settings.refresh_cookie_name, refresh_token)
    client.cookies.set(settings.csrf_cookie_name, csrf)
    return await client.post("/auth/refresh", headers={settings.csrf_header_name: csrf})


def _assert_both_cleared(response: Response) -> None:
    settings = get_settings()
    cleared = _cleared(response)
    assert settings.refresh_cookie_name in cleared, response.headers.get_list("set-cookie")
    assert f"Path={settings.refresh_cookie_path}" in cleared[settings.refresh_cookie_name]
    assert settings.csrf_cookie_name in cleared
    assert "Path=/" in cleared[settings.csrf_cookie_name]


@pytest.mark.asyncio
async def test_unknown_refresh_token_401_clears_cookies(client: AsyncClient) -> None:
    r = await _refresh(client, "not-a-real-token", "c")
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid refresh token"}
    _assert_both_cleared(r)


@pytest.mark.asyncio
async def test_reused_refresh_token_401_clears_cookies(client: AsyncClient) -> None:
    settings = get_settings()
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    t1 = client.cookies[settings.refresh_cookie_name]
    csrf = client.cookies[settings.csrf_cookie_name]
    assert (await _refresh(client, t1, csrf)).status_code == 200
    async with _write_sessionmaker()() as s:
        await s.execute(
            update(RefreshToken).values(created_at=func.now() - text("interval '1 hour'"))
        )
        await s.commit()

    r = await _refresh(client, t1, csrf)
    assert r.status_code == 401
    _assert_both_cleared(r)


@pytest.mark.asyncio
async def test_missing_refresh_cookie_401_clears_csrf_cookie(client: AsyncClient) -> None:
    settings = get_settings()
    client.cookies.set(settings.csrf_cookie_name, "c")
    r = await client.post("/auth/refresh", headers={settings.csrf_header_name: "c"})
    assert r.status_code == 401
    _assert_both_cleared(r)


@pytest.mark.asyncio
async def test_successful_refresh_still_sets_cookies(client: AsyncClient) -> None:
    settings = get_settings()
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    r = await _refresh(
        client,
        client.cookies[settings.refresh_cookie_name],
        client.cookies[settings.csrf_cookie_name],
    )
    assert r.status_code == 200
    assert _cleared(r) == {}
