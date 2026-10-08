"""ER2-01: an access token issued before the account's credentials changed is dead.

``users.credentials_changed_at`` is bumped by a password change, ``reset-2fa`` and
an admin disabling the account. Every access token carries that value as the
``cca`` claim (integer microseconds, or null); a token is accepted only when its
claim equals the column exactly, so a token issued in the same second just
before a change is refused and one issued just after is accepted. A genuine,
unexpired but revoked token gets 401 with ``X-Session-Revoked: true`` on every
route, public ones included. Tokens without the claim (issued before 0019) keep
working while the column is NULL, so the deploy signs nobody out.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from app.core.config import get_settings
from app.core.db import _write_sessionmaker
from app.core.redis import get_redis
from app.models.audit_log import AuditLog
from app.models.user import User, UserRole
from httpx import AsyncClient, Response
from sqlalchemy import select, text

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a different long passphrase"
MISSING = object()


def _email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"


async def _reset_limits() -> None:
    redis = get_redis()
    keys = [k async for k in redis.scan_iter(match="rl:*")]
    if keys:
        await redis.delete(*keys)


async def _login(client: AsyncClient, email: str, password: str) -> Response:
    await _reset_limits()
    return await client.post("/auth/login", json={"email": email, "password": password})


async def _signed_in(client: AsyncClient) -> tuple[str, str]:
    email = _email()
    r = await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = await _login(client, email, PASSWORD)
    assert r.status_code == 200, r.text
    return email, r.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _user(email: str) -> User:
    async with _write_sessionmaker()() as s:
        user = await s.scalar(select(User).where(User.email == email))
        assert user is not None
        return user


async def _changed_at(email: str) -> datetime | None:
    async with _write_sessionmaker()() as s:
        value = await s.scalar(
            text("SELECT credentials_changed_at FROM users WHERE email = :e"), {"e": email}
        )
        return value  # type: ignore[no-any-return]


async def _set_changed_at(email: str, value: datetime | None) -> None:
    async with _write_sessionmaker()() as s:
        await s.execute(
            text("UPDATE users SET credentials_changed_at = :v WHERE email = :e"),
            {"v": value, "e": email},
        )
        await s.commit()


def _token(user: User, *, iat: datetime, cca: Any = MISSING) -> str:
    """An access token as the app would sign it; ``cca`` MISSING = a pre-0019 token."""
    settings = get_settings()
    claims: dict[str, Any] = {
        "sub": str(user.id),
        "role": user.role.value,
        "type": "access",
        "iat": int(iat.timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if cca is not MISSING:
        claims["cca"] = cca
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


def _micros(value: datetime) -> int:
    return (value - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)


def _assert_revoked(r: Response) -> None:
    assert r.status_code == 401, r.text
    assert r.headers.get("X-Session-Revoked") == "true"


# --- password change ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_token_from_before_the_password_change_is_revoked(client: AsyncClient) -> None:
    email, old = await _signed_in(client)
    r = await client.post(
        "/auth/change-password",
        headers=_bearer(old),
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert r.status_code == 200, r.text
    assert await _changed_at(email) is not None

    _assert_revoked(await client.get("/auth/me", headers=_bearer(old)))
    # The automatic re-sign-in right after the change (same second) passes.
    new = (await _login(client, email, NEW_PASSWORD)).json()["access_token"]
    assert (await client.get("/auth/me", headers=_bearer(new))).status_code == 200


@pytest.mark.asyncio
async def test_the_same_second_is_decided_by_the_claim_not_the_clock(client: AsyncClient) -> None:
    email, before = await _signed_in(client)
    claims = jwt.decode(before, options={"verify_signature": False})
    # The change lands later in the very second that token was issued.
    await _set_changed_at(
        email, datetime.fromtimestamp(claims["iat"], UTC) + timedelta(milliseconds=900)
    )

    _assert_revoked(await client.get("/auth/me", headers=_bearer(before)))
    after = (await _login(client, email, PASSWORD)).json()["access_token"]
    assert jwt.decode(after, options={"verify_signature": False})["iat"] >= claims["iat"]
    assert (await client.get("/auth/me", headers=_bearer(after))).status_code == 200


@pytest.mark.asyncio
async def test_a_token_from_refresh_after_the_change_is_accepted(client: AsyncClient) -> None:
    email, old = await _signed_in(client)
    await client.post(
        "/auth/change-password",
        headers=_bearer(old),
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    await _login(client, email, NEW_PASSWORD)  # new refresh family in the client's cookie jar
    settings = get_settings()
    csrf = client.cookies.get(settings.csrf_cookie_name)
    r = await client.post(
        settings.refresh_cookie_path, headers={settings.csrf_header_name: csrf or ""}
    )
    assert r.status_code == 200, r.text
    refreshed = r.json()["access_token"]
    claim = jwt.decode(refreshed, options={"verify_signature": False})["cca"]
    changed = await _changed_at(email)
    assert changed is not None and claim == _micros(changed)
    assert (await client.get("/auth/me", headers=_bearer(refreshed))).status_code == 200


# --- public routes ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_routes_answer_401_for_a_revoked_token_but_guest_for_an_expired_one(
    client: AsyncClient,
) -> None:
    email, old = await _signed_in(client)
    await _set_changed_at(email, datetime.now(UTC))
    _assert_revoked(await client.get("/matches", headers=_bearer(old)))

    user = await _user(email)
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "role": "user",
            "type": "access",
            "iat": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
            "jti": "x",
        },
        get_settings().secret_key,
        algorithm=get_settings().jwt_algorithm,
    )
    r = await client.get("/matches", headers=_bearer(expired))
    assert r.status_code == 200  # unchanged F9 behaviour: guest view
    assert "X-Session-Revoked" not in r.headers


# --- claim edge cases ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pre_0019_token_without_the_claim_works_while_the_column_is_null(
    client: AsyncClient,
) -> None:
    """The deploy moment: rc6 tokens carry no claim and every column is NULL."""
    email, _token_now = await _signed_in(client)
    user = await _user(email)
    assert await _changed_at(email) is None
    legacy = _token(user, iat=datetime.now(UTC))
    assert (await client.get("/auth/me", headers=_bearer(legacy))).status_code == 200
    assert (await client.get("/matches", headers=_bearer(legacy))).status_code == 200


@pytest.mark.asyncio
async def test_a_claimless_token_is_judged_by_whole_seconds_once_the_column_is_set(
    client: AsyncClient,
) -> None:
    email, _ = await _signed_in(client)
    user = await _user(email)
    changed = datetime.now(UTC).replace(microsecond=400_000) - timedelta(minutes=1)
    await _set_changed_at(email, changed)

    earlier = _token(user, iat=changed - timedelta(seconds=5))
    same_second = _token(user, iat=changed)  # iat floors to the change's second
    later = _token(user, iat=changed + timedelta(seconds=1))
    _assert_revoked(await client.get("/auth/me", headers=_bearer(earlier)))
    _assert_revoked(await client.get("/auth/me", headers=_bearer(same_second)))
    assert (await client.get("/auth/me", headers=_bearer(later))).status_code == 200


@pytest.mark.asyncio
async def test_a_claim_with_a_null_column_is_refused(client: AsyncClient) -> None:
    """The column is never set back to NULL by the app; a claim against NULL means
    it was rewritten (a downgrade and upgrade of 0019): refuse, refresh repairs it."""
    email, _ = await _signed_in(client)
    user = await _user(email)
    stale = _token(user, iat=datetime.now(UTC), cca=_micros(datetime.now(UTC)))
    _assert_revoked(await client.get("/auth/me", headers=_bearer(stale)))


@pytest.mark.parametrize("garbage", ["1700000000000000", 1.5, True, [], {"a": 1}])
@pytest.mark.asyncio
async def test_a_garbage_claim_is_an_invalid_token_not_a_500(
    client: AsyncClient, garbage: Any
) -> None:
    email, _ = await _signed_in(client)
    user = await _user(email)
    token = _token(user, iat=datetime.now(UTC), cca=garbage)

    r = await client.get("/auth/me", headers=_bearer(token))
    assert r.status_code == 401
    assert "X-Session-Revoked" not in r.headers
    r = await client.get("/matches", headers=_bearer(token))
    assert r.status_code == 200  # an invalid token on a public route is a guest, as before


# --- reset-2fa and admin disable bump the same timestamp ------------------------------


@pytest.mark.asyncio
async def test_reset_2fa_revokes_live_access_tokens_and_audits_the_timestamp(
    client: AsyncClient,
) -> None:
    from app.cli import _reset_2fa

    email, old = await _signed_in(client)
    assert await _reset_2fa(email, require_password_change=False) == 0

    _assert_revoked(await client.get("/auth/me", headers=_bearer(old)))
    changed = await _changed_at(email)
    assert changed is not None
    async with _write_sessionmaker()() as s:
        audit = await s.scalar(
            select(AuditLog).where(AuditLog.action == "auth.2fa.reset_by_operator")
        )
        assert audit is not None and audit.meta is not None
        assert audit.meta["credentials_changed_at"] == changed.isoformat()


@pytest.mark.asyncio
async def test_disable_then_enable_does_not_bring_old_tokens_back(client: AsyncClient) -> None:
    from app.core.security import create_access_token

    email, old = await _signed_in(client)
    user = await _user(email)
    async with _write_sessionmaker()() as s:
        admin = User(
            email=f"admin-{uuid.uuid4().hex[:6]}@example.com",
            password_hash="x",
            role=UserRole.admin,
            totp_enabled=True,
            must_change_password=False,
        )
        s.add(admin)
        await s.commit()
        admin_token = create_access_token(subject=str(admin.id), role="admin")

    r = await client.post(f"/admin/users/{user.id}/disable", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text
    r = await client.post(f"/admin/users/{user.id}/enable", headers=_bearer(admin_token))
    assert r.status_code == 200, r.text

    _assert_revoked(await client.get("/auth/me", headers=_bearer(old)))
    changed = await _changed_at(email)
    assert changed is not None
    async with _write_sessionmaker()() as s:
        audit = await s.scalar(select(AuditLog).where(AuditLog.action == "user.disable"))
        assert audit is not None and audit.meta is not None
        assert audit.meta["credentials_changed_at"] == changed.isoformat()
    # The user signs in again normally.
    assert (await _login(client, email, PASSWORD)).status_code == 200
