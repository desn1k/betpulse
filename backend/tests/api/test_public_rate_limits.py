"""F7: per-caller limits on the public match routes and a per-IP limit on
/auth/refresh.

* ``GET /matches`` (the list), ``/matches/{id}`` and ``/matches/{id}/analysis``
  are limited **before tier resolution**: one dependency derives the limit
  identity from the access token's verified subject (signature and expiry, no
  database read) or the guest's IP bucket, and runs before ``get_tier_context``.
* ``POST /auth/refresh`` is limited per client IP (IPv6 per /64) before the
  rotation. It fails **open** when Redis is unavailable (refresh needs Redis for
  nothing else); the match routes fail closed (their quotas live in Redis).

The default test client is a trusted proxy (127.0.0.1), so X-Forwarded-For
picks the client.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from app.core import deps
from app.core.config import Settings, get_settings
from app.core.deps import get_settings_dep, rate_limit_identity
from app.core.security import ACCESS_TOKEN_TYPE, create_access_token
from app.models.user import User, UserRole
from app.services import rate_limit
from httpx import AsyncClient, Response
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.api.test_tiers import _seed_match

PASSWORD = "correct horse battery staple"


def _guest(ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip}


def _signed(claims: dict[str, Any]) -> str:
    """A token with a valid signature and expiry and the given extra claims."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "type": ACCESS_TOKEN_TYPE,
        "role": "user",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "jti": uuid.uuid4().hex,
        "cca": None,
    }
    payload.update(claims)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


@pytest.fixture
def small_limits() -> Iterator[Settings]:
    """Every public limit at 3 per window, so the tests stay fast."""
    from app.main import app

    settings = get_settings().model_copy(
        update={
            "rate_limit_match_list_per_window": 3,
            "rate_limit_match_list_window_seconds": 30,
            "rate_limit_match_detail_per_window": 3,
            "rate_limit_llm_analysis_per_minute": 3,
            "rate_limit_refresh_per_minute": 3,
        }
    )
    app.dependency_overrides[get_settings_dep] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings_dep, None)


# --- defaults -------------------------------------------------------------------


def test_defaults() -> None:
    settings = get_settings()
    assert settings.rate_limit_match_list_per_window == 120
    assert settings.rate_limit_match_list_window_seconds == 60
    assert settings.rate_limit_refresh_per_minute == 120


@pytest.mark.parametrize(
    "field",
    [
        "rate_limit_match_list_per_window",
        "rate_limit_match_list_window_seconds",
        "rate_limit_refresh_per_minute",
    ],
)
def test_limits_must_be_positive(field: str) -> None:
    with pytest.raises(ValueError, match=field.upper()):
        Settings.model_validate({field: 0})


# --- the match list -------------------------------------------------------------


async def test_121st_list_request_gets_429(client: AsyncClient) -> None:
    ip = "198.51.100.40"
    for _ in range(120):
        assert (await client.get("/matches", headers=_guest(ip))).status_code == 200
    limited = await client.get("/matches", headers=_guest(ip))
    assert limited.status_code == 429
    assert limited.json()["detail"] == "Too many match list requests"
    assert 0 < int(limited.headers["Retry-After"]) <= 60


async def test_list_limit_is_per_identity(
    client: AsyncClient, session: AsyncSession, small_limits: Settings
) -> None:
    for _ in range(3):
        await client.get("/matches", headers=_guest("198.51.100.41"))
    assert (await client.get("/matches", headers=_guest("198.51.100.41"))).status_code == 429
    # Another guest, and the same /64 versus another one.
    assert (await client.get("/matches", headers=_guest("198.51.100.42"))).status_code == 200
    for n in range(3):
        await client.get("/matches", headers=_guest(f"2001:db8:40:1::{n + 1}"))
    assert (await client.get("/matches", headers=_guest("2001:db8:40:1::ff"))).status_code == 429
    assert (await client.get("/matches", headers=_guest("2001:db8:40:2::1"))).status_code == 200
    # A signed-in caller behind the limited address is counted by user id.
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", role=UserRole.user)
    session.add(user)
    await session.commit()
    token = create_access_token(subject=str(user.id), role="user")
    headers = {**_guest("198.51.100.41"), "Authorization": f"Bearer {token}"}
    assert (await client.get("/matches", headers=headers)).status_code == 200


async def test_list_window_comes_from_settings(client: AsyncClient, small_limits: Settings) -> None:
    for _ in range(3):
        await client.get("/matches", headers=_guest("198.51.100.43"))
    limited = await client.get("/matches", headers=_guest("198.51.100.43"))
    assert limited.status_code == 429
    assert 0 < int(limited.headers["Retry-After"]) <= 30


# --- before tier resolution -----------------------------------------------------


def _paths(match_id: uuid.UUID) -> list[str]:
    return ["/matches", f"/matches/{match_id}", f"/matches/{match_id}/analysis"]


async def test_over_the_limit_no_tier_is_resolved_for_a_guest(
    client: AsyncClient,
    session: AsyncSession,
    small_limits: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = await _seed_match(session)
    await session.commit()
    for path in _paths(fixture.id):
        for _ in range(3):
            await client.get(path, headers=_guest("198.51.100.44"))
    _forbid(monkeypatch)
    for path in _paths(fixture.id):
        response = await client.get(path, headers=_guest("198.51.100.44"))
        assert response.status_code == 429, path


async def test_over_the_limit_no_user_is_read_for_a_signed_in_caller(
    client: AsyncClient,
    session: AsyncSession,
    small_limits: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = await _seed_match(session)
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", role=UserRole.user)
    session.add(user)
    await session.commit()
    token = create_access_token(subject=str(user.id), role="user")
    headers = {"Authorization": f"Bearer {token}", **_guest("198.51.100.45")}
    for path in _paths(fixture.id):
        for _ in range(3):
            await client.get(path, headers=headers)
    _forbid(monkeypatch)
    for path in _paths(fixture.id):
        response = await client.get(path, headers=headers)
        assert response.status_code == 429, path


def _forbid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over the limit, nothing may resolve the tier or read the user."""

    async def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("tier resolved or user read before the rate limit")

    monkeypatch.setattr(deps, "resolve_tier_context", _forbidden)
    monkeypatch.setattr(AsyncSession, "get", _forbidden)


# --- the limit identity ---------------------------------------------------------


def test_identity_of_a_valid_token_is_its_subject() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(subject=str(user_id), role="user")
    assert rate_limit_identity(token, "198.51.100.46") == str(user_id)


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": 123},
        {"sub": ["a"]},
        {"sub": None},
        {"sub": "not-a-uuid"},
        {"sub": ""},
        {},  # no sub at all
    ],
    ids=["int", "list", "null", "not-a-uuid", "empty", "missing"],
)
def test_a_signed_token_with_a_garbage_subject_is_a_guest(claims: dict[str, Any]) -> None:
    token = _signed(claims)
    identity = rate_limit_identity(token, "2001:db8:46:1::9")
    assert identity == "2001:db8:46:1::/64"


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        "a.b.c",
        jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "access"},
            "a-different-key-of-32-bytes-0000",
            algorithm="HS256",
        ),
    ],
    ids=["garbage", "three-parts", "wrong-key"],
)
def test_an_unverifiable_token_is_a_guest(token: str) -> None:
    assert rate_limit_identity(token, "198.51.100.47") == "198.51.100.47"


def test_an_expired_token_is_a_guest() -> None:
    expired = _signed(
        {
            "sub": str(uuid.uuid4()),
            "exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
        }
    )
    assert rate_limit_identity(expired, "198.51.100.48") == "198.51.100.48"


def test_no_token_is_a_guest() -> None:
    assert rate_limit_identity(None, "198.51.100.49") == "198.51.100.49"


@pytest.mark.parametrize("sub", [str(uuid.uuid4()), "not-a-uuid", "x" * 500])
def test_the_identity_never_carries_token_material(sub: str) -> None:
    token = _signed({"sub": sub})
    identity = rate_limit_identity(token, "198.51.100.50")
    assert token not in identity
    for part in token.split("."):
        assert part not in identity
    # Only a canonical UUID or the guest's bucket.
    assert identity == "198.51.100.50" or str(uuid.UUID(identity)) == identity


async def test_garbage_subject_through_the_routes(client: AsyncClient) -> None:
    token = _signed({"sub": 123})
    headers = {"Authorization": f"Bearer {token}", **_guest("198.51.100.51")}
    # A public route treats it as a guest; a protected one refuses it. Neither
    # fails with a 500.
    assert (await client.get("/matches", headers=headers)).status_code == 200
    me = await client.get("/auth/me", headers=headers)
    assert me.status_code == 401


# --- /auth/refresh --------------------------------------------------------------


async def _refresh(client: AsyncClient, ip: str, cookie: str | None = None) -> Response:
    """POST /auth/refresh from ``ip`` with this refresh cookie (an unknown one by
    default: refused, but every attempt counts)."""
    settings = get_settings()
    client.cookies.clear()
    client.cookies.set(settings.refresh_cookie_name, cookie or uuid.uuid4().hex)
    client.cookies.set(settings.csrf_cookie_name, "csrf")
    return await client.post(
        "/auth/refresh", headers={settings.csrf_header_name: "csrf", **_guest(ip)}
    )


async def _sign_in(client: AsyncClient) -> str:
    """Register and sign in; the refresh token from the cookie."""
    settings = get_settings()
    email = f"rl-{uuid.uuid4().hex[:8]}@example.com"
    assert (
        await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    ).status_code == 201
    assert (
        await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    ).status_code == 200
    return client.cookies[settings.refresh_cookie_name]


async def test_121st_refresh_from_one_ip_gets_429(client: AsyncClient) -> None:
    ip = "198.51.100.60"
    for _ in range(120):
        # Every attempt counts, a refused one included.
        assert (await _refresh(client, ip)).status_code == 401
    limited = await _refresh(client, ip)
    assert limited.status_code == 429
    assert 0 < int(limited.headers["Retry-After"]) <= 60
    # Another address is not affected.
    assert (await _refresh(client, "198.51.100.61")).status_code == 401


async def test_a_limited_refresh_does_not_rotate(
    client: AsyncClient, small_limits: Settings
) -> None:
    t1 = await _sign_in(client)
    ip = "198.51.100.62"
    for _ in range(3):
        await _refresh(client, ip)
    limited = await _refresh(client, ip, t1)
    assert limited.status_code == 429
    # No rotation and no cookie change: T1 is still the live token.
    assert "set-cookie" not in limited.headers
    assert (await _refresh(client, "198.51.100.63", t1)).status_code == 200


async def test_refresh_limit_buckets_ipv6_by_64(
    client: AsyncClient, small_limits: Settings
) -> None:
    for n in range(3):
        await _refresh(client, f"2001:db8:60:1::{n + 1}")
    assert (await _refresh(client, "2001:db8:60:1::ff")).status_code == 429
    assert (await _refresh(client, "2001:db8:60:2::1")).status_code == 401


async def test_refresh_fails_open_when_redis_is_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    t1 = await _sign_in(client)

    async def _down(*_args: object, **_kwargs: object) -> int:
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(rate_limit, "incr_with_ttl", _down)
    monkeypatch.setattr(rate_limit.logger, "disabled", False)
    response = await _refresh(client, "198.51.100.64", t1)
    assert response.status_code == 200
    assert any("refresh rate limit not applied" in r.getMessage() for r in caplog.records)


async def test_a_stalled_redis_delays_a_refresh_at_most_briefly(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    t1 = await _sign_in(client)

    async def _stalled(*_args: object, **_kwargs: object) -> int:
        await asyncio.sleep(30)
        return 1

    monkeypatch.setattr(rate_limit, "incr_with_ttl", _stalled)
    started = time.monotonic()
    response = await _refresh(client, "198.51.100.65", t1)
    assert response.status_code == 200
    assert time.monotonic() - started < 2 * rate_limit.REFRESH_LIMIT_TIMEOUT_SECONDS + 1


async def test_the_list_fails_closed_when_redis_is_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _down(*_args: object, **_kwargs: object) -> int:
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(rate_limit, "incr_with_ttl", _down)
    with pytest.raises(RedisConnectionError):
        await client.get("/matches", headers=_guest("198.51.100.66"))
