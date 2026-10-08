"""Account security routes behind the admin first-login flow (F10, F11).

Password change and the TOTP routes are reachable from the UI now, so each is
limited per user **before** its expensive or guessable check (Argon2 for the
password, the 6-digit code for TOTP: ER2-02), a wrong code's audit row survives
the 400, and TOTP setup can no longer reset an enabled second factor (F11).

Every test drives the real app over HTTP against real Postgres and Redis.
"""

from __future__ import annotations

import uuid
from typing import Any

import pyotp
import pytest
from app.core.db import _write_sessionmaker
from app.core.redis import get_redis
from app.core.security import verify_password, verify_totp
from app.models.audit_log import AuditLog
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.services import auth as auth_service
from app.services import twofa as twofa_service
from app.services.audit import AuditAction
from httpx import AsyncClient, Response
from sqlalchemy import func, select

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a different long passphrase"
WRONG = "definitely-not-the-password"


def _email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"


async def _reset_limits(pattern: str) -> None:
    redis = get_redis()
    keys = [k async for k in redis.scan_iter(match=pattern)]
    if keys:
        await redis.delete(*keys)


async def _reset_login_ip_limit() -> None:
    await _reset_limits("rl:login:*")


async def _login(
    client: AsyncClient, email: str, password: str, code: str | None = None
) -> Response:
    await _reset_login_ip_limit()
    body: dict[str, Any] = {"email": email, "password": password}
    if code is not None:
        body["totp_code"] = code
    return await client.post("/auth/login", json=body)


async def _signed_in(client: AsyncClient) -> tuple[str, dict[str, str]]:
    email = _email()
    r = await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = await _login(client, email, PASSWORD)
    assert r.status_code == 200, r.text
    return email, {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _with_totp(client: AsyncClient) -> tuple[str, dict[str, str], str]:
    email, auth = await _signed_in(client)
    secret = (await client.post("/auth/2fa/setup", headers=auth)).json()["secret"]
    r = await client.post("/auth/2fa/enable", headers=auth, json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return email, auth, secret


async def _user(email: str) -> User:
    async with _write_sessionmaker()() as s:
        user = await s.scalar(select(User).where(User.email == email))
        assert user is not None
        return user


async def _audit_count(action: str, user_id: uuid.UUID) -> int:
    async with _write_sessionmaker()() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == action, AuditLog.actor_user_id == user_id)
            )
            or 0
        )


def _wrong_code(secret: str) -> str:
    """A 6-digit code that is not valid in any accepted step."""
    totp = pyotp.TOTP(secret)
    for candidate in ("000000", "111111", "222222", "333333"):
        if not totp.verify(candidate, valid_window=1):
            return candidate
    raise AssertionError("no invalid code found")


class _Spy:
    def __init__(self, target: Any) -> None:
        self.target = target
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self.target(*args, **kwargs)


# --- F11: setup never resets an enabled second factor --------------------------


@pytest.mark.asyncio
async def test_setup_is_refused_while_totp_is_enabled(client: AsyncClient) -> None:
    email, auth, secret = await _with_totp(client)
    before = await _user(email)

    r = await client.post("/auth/2fa/setup", headers=auth)

    assert r.status_code == 409
    assert r.json() == {"detail": "two_factor_already_enabled"}
    after = await _user(email)
    assert after.totp_enabled is True
    assert after.totp_secret_encrypted == before.totp_secret_encrypted
    # The old secret still signs the user in.
    assert (await _login(client, email, PASSWORD, pyotp.TOTP(secret).now())).status_code == 200


@pytest.mark.asyncio
async def test_rotation_goes_through_disable_with_a_valid_code(client: AsyncClient) -> None:
    _email_addr, auth, secret = await _with_totp(client)
    r = await client.post(
        "/auth/2fa/disable", headers=auth, json={"code": pyotp.TOTP(secret).now()}
    )
    assert r.status_code == 200, r.text
    r = await client.post("/auth/2fa/setup", headers=auth)
    assert r.status_code == 200
    assert r.json()["secret"] != secret


# --- ER2-02: per-user limits run before the expensive or guessable check --------


@pytest.mark.asyncio
async def test_password_change_is_limited_before_argon2(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _email_addr, auth = await _signed_in(client)
    spy = _Spy(verify_password)
    monkeypatch.setattr(auth_service, "verify_password", spy)
    body = {"current_password": WRONG, "new_password": NEW_PASSWORD}

    for _ in range(5):
        assert (
            await client.post("/auth/change-password", headers=auth, json=body)
        ).status_code == 400
    r = await client.post("/auth/change-password", headers=auth, json=body)

    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    assert spy.calls == 5


@pytest.mark.parametrize("route", ["/auth/2fa/enable", "/auth/2fa/disable"])
@pytest.mark.asyncio
async def test_totp_code_routes_are_limited_before_the_code_check(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    if route.endswith("enable"):
        _email_addr, auth = await _signed_in(client)
        secret = (await client.post("/auth/2fa/setup", headers=auth)).json()["secret"]
    else:
        _email_addr, auth, secret = await _with_totp(client)
        # enable and disable share one bucket; start from the enable above.
        await _reset_limits("rl:totp_code:*")
    spy = _Spy(verify_totp)
    monkeypatch.setattr(twofa_service, "verify_totp", spy)
    body = {"code": _wrong_code(secret)}

    for _ in range(5):
        assert (await client.post(route, headers=auth, json=body)).status_code == 400
    r = await client.post(route, headers=auth, json=body)

    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    assert spy.calls == 5


@pytest.mark.asyncio
async def test_totp_setup_is_limited(client: AsyncClient) -> None:
    _email_addr, auth = await _signed_in(client)
    for _ in range(5):
        assert (await client.post("/auth/2fa/setup", headers=auth)).status_code == 200
    r = await client.post("/auth/2fa/setup", headers=auth)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


# --- a wrong code is audited durably (security state committed before raising) --


@pytest.mark.parametrize("route", ["/auth/2fa/enable", "/auth/2fa/disable"])
@pytest.mark.asyncio
async def test_wrong_code_audit_survives_the_400(client: AsyncClient, route: str) -> None:
    if route.endswith("enable"):
        email, auth = await _signed_in(client)
        secret = (await client.post("/auth/2fa/setup", headers=auth)).json()["secret"]
    else:
        email, auth, secret = await _with_totp(client)

    r = await client.post(route, headers=auth, json={"code": _wrong_code(secret)})

    assert r.status_code == 400
    assert r.json() == {"detail": "Invalid code"}
    user = await _user(email)
    assert await _audit_count(AuditAction.TWOFA_FAILURE, user.id) == 1


# --- the login's code step is no faster to brute-force than the password -------


@pytest.mark.asyncio
async def test_wrong_codes_at_login_lock_the_account_and_reveal_nothing(
    client: AsyncClient,
) -> None:
    email, _auth, secret = await _with_totp(client)
    wrong = _wrong_code(secret)
    wrong_password = await _login(client, email, WRONG)
    assert wrong_password.status_code == 401
    # Start the per-account count from zero after that probe.
    async with _write_sessionmaker()() as s:
        user = await s.scalar(select(User).where(User.email == email))
        assert user is not None
        user.failed_login_count = 0
        user.locked_until = None
        await s.commit()

    for _ in range(5):
        r = await _login(client, email, PASSWORD, wrong)
        # Same answer as a wrong password: no hint that the password was right.
        assert r.status_code == 401
        assert r.json() == wrong_password.json() == {"detail": "Invalid credentials"}
        assert "X-2FA-Required" not in r.headers

    # Locked: even the correct code is refused now.
    r = await _login(client, email, PASSWORD, pyotp.TOTP(secret).now())
    assert r.status_code == 429
    assert r.json() == {"detail": "Account temporarily locked"}
    user = await _user(email)
    assert user.failed_login_count >= 5
    assert await _audit_count(AuditAction.TWOFA_FAILURE, user.id) == 5


@pytest.mark.asyncio
async def test_the_code_step_shares_the_per_ip_login_limit(client: AsyncClient) -> None:
    email, _auth, secret = await _with_totp(client)
    wrong = _wrong_code(secret)
    await _reset_login_ip_limit()
    body = {"email": email, "password": PASSWORD, "totp_code": wrong}
    responses = [await client.post("/auth/login", json=body) for _ in range(6)]
    # The per-IP window (5/min) runs before the account check, code step included.
    assert [r.status_code for r in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429
    assert responses[5].json() == {"detail": "Too many login attempts"}


# --- reset-2fa: the operator's path for a lost authenticator ------------------


@pytest.mark.asyncio
async def test_reset_2fa_turns_totp_off_revokes_sessions_and_audits(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.cli import _reset_2fa

    email, _auth, secret = await _with_totp(client)

    assert await _reset_2fa(email.upper(), require_password_change=True) == 0

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert out[0].startswith(f"reset-2fa: {email}: two-factor authentication turned off")
    assert secret not in out[0]
    user = await _user(email)
    assert user.totp_enabled is False
    assert user.totp_secret_encrypted is None
    assert user.must_change_password is True
    async with _write_sessionmaker()() as s:
        live = await s.scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False))
        )
        assert live == 0
        audit = await s.scalar(
            select(AuditLog).where(AuditLog.action == AuditAction.TWOFA_RESET_BY_OPERATOR)
        )
        assert audit is not None
        assert audit.actor_user_id is None
        assert audit.target == email
    # Password alone signs in again.
    assert (await _login(client, email, PASSWORD)).status_code == 200


@pytest.mark.asyncio
async def test_reset_2fa_unknown_email_changes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    from app.cli import _reset_2fa

    assert await _reset_2fa("nobody@example.com", require_password_change=False) == 1
    assert capsys.readouterr().out.strip() == "reset-2fa: no user with that email; nothing changed"
    async with _write_sessionmaker()() as s:
        count = await s.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == AuditAction.TWOFA_RESET_BY_OPERATOR)
        )
        assert count == 0


# --- the client learns whether the server requires TOTP for this account -------


@pytest.mark.asyncio
async def test_me_reports_whether_two_factor_is_required(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    email, auth = await _signed_in(client)
    me = (await client.get("/auth/me", headers=auth)).json()
    assert me["two_factor_required"] is False  # a regular user: optional
    assert me["totp_enabled"] is False
    assert me["must_change_password"] is False

    async with _write_sessionmaker()() as s:
        user = await s.scalar(select(User).where(User.email == email))
        assert user is not None
        user.role = UserRole.admin
        await s.commit()
    assert (await client.get("/auth/me", headers=auth)).json()["two_factor_required"] is True

    monkeypatch.setattr(get_settings(), "admin_2fa_required", False)
    assert (await client.get("/auth/me", headers=auth)).json()["two_factor_required"] is False


@pytest.mark.asyncio
async def test_an_abandoned_setup_leaves_totp_off_and_a_fresh_setup_works(
    client: AsyncClient,
) -> None:
    email, auth = await _signed_in(client)
    first = (await client.post("/auth/2fa/setup", headers=auth)).json()["secret"]
    # The user leaves the page without confirming a code.
    assert (await _user(email)).totp_enabled is False
    assert (await _login(client, email, PASSWORD)).status_code == 200  # no code asked

    second = (await client.post("/auth/2fa/setup", headers=auth)).json()["secret"]
    assert second != first
    r = await client.post("/auth/2fa/enable", headers=auth, json={"code": pyotp.TOTP(first).now()})
    assert r.status_code == 400  # the abandoned secret is gone
    r = await client.post("/auth/2fa/enable", headers=auth, json={"code": pyotp.TOTP(second).now()})
    assert r.status_code == 200
