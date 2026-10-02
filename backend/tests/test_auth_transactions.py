"""HTTP-level integration tests for auth security state vs. request transactions.

The request session (``get_session``) rolls back on *any* exception, including
the ``HTTPException`` a handler raises to turn a domain error into a 401/429.
Security state written on those failure paths — failed-login counters, the
lockout timestamp, token-family revocation and their audit rows — must survive
that rollback, and refresh-token rotation must be atomic under concurrency.

Every test drives the real app over HTTP against real Postgres and Redis and
then inspects the database through an independent session, so it observes only
what was actually committed.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pyotp
import pytest
from app.core.config import get_settings
from app.core.db import _write_sessionmaker, reset_engines
from app.core.redis import get_redis
from app.core.security import hash_token
from app.models.audit_log import AuditLog
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import auth as auth_service
from app.services.audit import AuditAction
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select, text, update

PASSWORD = "correct horse battery staple"
WRONG = "definitely-not-the-password"


def _email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"


async def _reset_login_ip_limit() -> None:
    """Clear the per-IP login window so per-account lockout is what's tested."""
    redis = get_redis()
    keys = [k async for k in redis.scan_iter(match="rl:login:*")]
    if keys:
        await redis.delete(*keys)


async def _login(client: AsyncClient, email: str, password: str) -> Response:
    await _reset_login_ip_limit()
    return await client.post("/auth/login", json={"email": email, "password": password})


async def _register_and_login(client: AsyncClient, email: str) -> None:
    r = await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = await _login(client, email, PASSWORD)
    assert r.status_code == 200, r.text


@asynccontextmanager
async def _bare_client(cookies: dict[str, str]) -> AsyncIterator[AsyncClient]:
    """A fresh client carrying exactly ``cookies`` (no shared cookie jar), so a
    replayed or concurrent refresh sends the token we choose."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as c:
        yield c


async def _refresh_with(refresh_token: str, csrf: str) -> Response:
    settings = get_settings()
    cookies = {settings.refresh_cookie_name: refresh_token, settings.csrf_cookie_name: csrf}
    async with _bare_client(cookies) as c:
        return await c.post(settings.refresh_cookie_path, headers={settings.csrf_header_name: csrf})


def _session_cookies(client: AsyncClient) -> tuple[str, str]:
    settings = get_settings()
    return client.cookies[settings.refresh_cookie_name], client.cookies[settings.csrf_cookie_name]


async def _db_scalar(stmt: Any) -> Any:
    async with _write_sessionmaker()() as s:
        return await s.scalar(stmt)


async def _user(email: str) -> User:
    user = await _db_scalar(select(User).where(User.email == email))
    assert user is not None
    return user  # type: ignore[no-any-return]


async def _audit_count(action: str, actor: uuid.UUID | None = None) -> int:
    stmt = select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
    if actor is not None:
        stmt = stmt.where(AuditLog.actor_user_id == actor)
    return int(await _db_scalar(stmt))


async def _live_tokens(user_id: uuid.UUID) -> int:
    return int(
        await _db_scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
        )
    )


async def _backdate_refresh_tokens(user_id: uuid.UUID, *, seconds: int) -> None:
    """Age every token of ``user_id`` so a replay falls outside any grace window."""
    async with _write_sessionmaker()() as s:
        await s.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .values(created_at=func.now() - text(f"interval '{int(seconds)} seconds'"))
        )
        await s.commit()


# --- C-02: failed-login state survives the 401 ----------------------------


@pytest.mark.asyncio
async def test_wrong_passwords_persist_counter_and_audit(client: AsyncClient) -> None:
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})

    for _ in range(3):
        r = await _login(client, email, WRONG)
        assert r.status_code == 401

    user = await _user(email)
    assert user.failed_login_count == 3
    assert await _audit_count(AuditAction.LOGIN_FAILURE, user.id) == 3


@pytest.mark.asyncio
async def test_unknown_email_failure_is_audited(client: AsyncClient) -> None:
    r = await _login(client, "ghost@example.com", WRONG)
    assert r.status_code == 401
    assert await _audit_count(AuditAction.LOGIN_FAILURE) == 1


@pytest.mark.asyncio
async def test_lockout_triggers_at_threshold_and_blocks_correct_password(
    client: AsyncClient,
) -> None:
    settings = get_settings()
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})

    for _ in range(settings.login_max_failures - 1):
        assert (await _login(client, email, WRONG)).status_code == 401
    assert (await _user(email)).locked_until is None

    # The threshold-th failure sets the lock.
    assert (await _login(client, email, WRONG)).status_code == 401
    user = await _user(email)
    assert user.failed_login_count == settings.login_max_failures
    assert user.locked_until is not None

    # Locked: even the correct password is rejected, with a Retry-After.
    r = await _login(client, email, PASSWORD)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    assert await _audit_count(AuditAction.LOGIN_FAILURE, user.id) == settings.login_max_failures
    assert await _audit_count(AuditAction.LOGIN_LOCKED, user.id) == 1

    # After expiry the correct password works again and resets the counter.
    async with _write_sessionmaker()() as s:
        await s.execute(
            update(User)
            .where(User.id == user.id)
            .values(locked_until=func.now() - text("interval '1 second'"))
        )
        await s.commit()
    assert (await _login(client, email, PASSWORD)).status_code == 200
    user = await _user(email)
    assert user.failed_login_count == 0
    assert user.locked_until is None


@pytest.mark.asyncio
async def test_wrong_totp_persists_counter_and_audit(client: AsyncClient) -> None:
    email = _email()
    await _register_and_login(client, email)
    access = (await _login(client, email, PASSWORD)).json()["access_token"]
    auth = {"Authorization": f"Bearer {access}"}
    secret = (await client.post("/auth/2fa/setup", headers=auth)).json()["secret"]
    code = pyotp.TOTP(secret).now()
    assert (await client.post("/auth/2fa/enable", headers=auth, json={"code": code})).is_success

    await _reset_login_ip_limit()
    r = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD, "totp_code": "000000"}
    )
    assert r.status_code == 401

    user = await _user(email)
    assert user.failed_login_count == 1
    assert await _audit_count(AuditAction.TWOFA_FAILURE, user.id) == 1


# --- C-01: reuse of a rotated token revokes the family, durably -----------


@pytest.mark.asyncio
async def test_rotated_token_replay_revokes_family_and_persists(client: AsyncClient) -> None:
    email = _email()
    await _register_and_login(client, email)
    user = await _user(email)
    t1, csrf = _session_cookies(client)

    r = await _refresh_with(t1, csrf)
    assert r.status_code == 200, r.text
    t2 = r.cookies[get_settings().refresh_cookie_name]
    assert t2 != t1

    # Replay the rotated token well outside any benign-duplicate window.
    await _backdate_refresh_tokens(user.id, seconds=3600)
    r = await _refresh_with(t1, csrf)
    assert r.status_code == 401

    # The revocation and its audit row were committed despite the 401 ...
    assert await _live_tokens(user.id) == 0
    assert await _audit_count(AuditAction.TOKEN_REUSE_DETECTED, user.id) == 1
    # ... so the legitimately rotated descendant is dead too.
    assert (await _refresh_with(t2, csrf)).status_code == 401


# --- H-01: rotation is atomic under concurrency ---------------------------


@pytest.fixture
def widen_rotation_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold each rotation open just after the token row has been read, so every
    concurrent request reaches its decision before any of them commits — the
    window a real deployment hits under load. A correct implementation
    serialises on the token family, so the delay only slows it down."""
    original = auth_service.get_user_by_id

    async def slow_get_user_by_id(*args: Any, **kwargs: Any) -> User | None:
        await asyncio.sleep(0.2)
        return await original(*args, **kwargs)

    monkeypatch.setattr(auth_service, "get_user_by_id", slow_get_user_by_id)


@pytest.mark.asyncio
@pytest.mark.usefixtures("widen_rotation_race")
async def test_parallel_refresh_with_same_token_has_single_winner(client: AsyncClient) -> None:
    email = _email()
    await _register_and_login(client, email)
    user = await _user(email)
    t1, csrf = _session_cookies(client)

    responses = await asyncio.gather(*(_refresh_with(t1, csrf) for _ in range(6)))
    statuses = sorted(r.status_code for r in responses)

    # Exactly one winner; every loser is a benign duplicate (409), not theft.
    assert statuses == [200, 409, 409, 409, 409, 409], statuses
    assert await _live_tokens(user.id) == 1
    assert await _audit_count(AuditAction.TOKEN_REFRESH, user.id) == 1
    assert await _audit_count(AuditAction.TOKEN_REFRESH_CONFLICT, user.id) == 5
    assert await _audit_count(AuditAction.TOKEN_REUSE_DETECTED, user.id) == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("widen_rotation_race")
async def test_benign_duplicate_refresh_keeps_session_alive(client: AsyncClient) -> None:
    settings = get_settings()
    email = _email()
    await _register_and_login(client, email)
    user = await _user(email)
    t1, csrf = _session_cookies(client)

    first, second = await asyncio.gather(_refresh_with(t1, csrf), _refresh_with(t1, csrf))
    winner, loser = (first, second) if first.status_code == 200 else (second, first)
    assert (winner.status_code, loser.status_code) == (200, 409)
    assert loser.headers["Retry-After"] == "1"
    # The loser must not clear (or replace) the cookies the winner just set.
    assert "set-cookie" not in loser.headers
    t2 = winner.cookies[settings.refresh_cookie_name]

    # Not treated as theft: the family stays live and the client carries on.
    assert await _audit_count(AuditAction.TOKEN_REUSE_DETECTED, user.id) == 0
    r = await _refresh_with(t2, winner.cookies[settings.csrf_cookie_name])
    assert r.status_code == 200, r.text
    assert await _live_tokens(user.id) == 1


@pytest.mark.asyncio
async def test_replay_within_grace_after_descendant_rotated_is_reuse(
    client: AsyncClient,
) -> None:
    """The grace window covers only a duplicate of the *latest* rotation: once
    the replacement has itself been rotated, the old token is a replay."""
    email = _email()
    await _register_and_login(client, email)
    user = await _user(email)
    t1, csrf = _session_cookies(client)

    t2 = (await _refresh_with(t1, csrf)).cookies[get_settings().refresh_cookie_name]
    assert (await _refresh_with(t2, csrf)).status_code == 200

    assert (await _refresh_with(t1, csrf)).status_code == 401
    assert await _live_tokens(user.id) == 0
    assert await _audit_count(AuditAction.TOKEN_REUSE_DETECTED, user.id) == 1


@pytest.mark.asyncio
async def test_replay_after_logout_is_rejected_and_audited(client: AsyncClient) -> None:
    settings = get_settings()
    email = _email()
    await _register_and_login(client, email)
    user = await _user(email)
    t1, csrf = _session_cookies(client)

    cookies = {settings.refresh_cookie_name: t1, settings.csrf_cookie_name: csrf}
    async with _bare_client(cookies) as c:
        r = await c.post("/auth/logout", headers={settings.csrf_header_name: csrf})
    assert r.status_code == 200
    assert await _live_tokens(user.id) == 0

    assert (await _refresh_with(t1, csrf)).status_code == 401
    assert await _audit_count(AuditAction.TOKEN_REUSE_DETECTED, user.id) == 1


# --- Audit hygiene ---------------------------------------------------------


@pytest.mark.asyncio
async def test_security_audit_rows_carry_no_secrets_or_unknown_emails(
    client: AsyncClient,
) -> None:
    email = _email()
    await _register_and_login(client, email)
    user = await _user(email)
    t1, csrf = _session_cookies(client)
    t2 = (await _refresh_with(t1, csrf)).cookies[get_settings().refresh_cookie_name]
    await _backdate_refresh_tokens(user.id, seconds=3600)
    assert (await _refresh_with(t1, csrf)).status_code == 401

    ghost = "Ghost.Person@Example.com"
    assert (await _login(client, ghost, WRONG)).status_code == 401
    assert (await _login(client, email, WRONG)).status_code == 401

    async with _write_sessionmaker()() as s:
        rows = (await s.scalars(select(AuditLog))).all()
    dumped = " ".join(f"{r.action} {r.target} {r.meta}" for r in rows)
    for secret in (t1, t2, hash_token(t1), hash_token(t2), csrf, WRONG, PASSWORD):
        assert secret not in dumped
    assert "ghost.person" not in dumped.lower()

    unknown = [r for r in rows if (r.meta or {}).get("reason") == "unknown_or_inactive"]
    assert len(unknown) == 1
    assert unknown[0].target is not None
    assert unknown[0].target.startswith("email-hmac:")
    # Stable per address, so repeated attempts on one email still correlate.
    assert (await _login(client, ghost, WRONG)).status_code == 401
    async with _write_sessionmaker()() as s:
        targets = (
            await s.scalars(
                select(AuditLog.target).where(AuditLog.action == AuditAction.LOGIN_FAILURE)
            )
        ).all()
    assert targets.count(unknown[0].target) == 2


# --- Connection pools: no hold-and-wait deadlock --------------------------


@pytest.fixture
def tiny_pools(monkeypatch: pytest.MonkeyPatch) -> float:
    """One connection in the request pool and one in the security pool.

    A failed login holds its request connection while it opens the security
    transaction. Were both drawn from one pool, a pool of 1 would deadlock on
    the very first failure (and a default 5+10 pool on 15 concurrent ones) until
    ``pool_timeout``. Returns that timeout."""
    settings = get_settings()
    for name, value in {
        "db_pool_size": 1,
        "db_max_overflow": 0,
        "db_security_pool_size": 1,
        "db_security_max_overflow": 0,
        "db_pool_timeout_seconds": 10,
        "rate_limit_login_per_minute": 10_000,
        "login_max_failures": 100,
    }.items():
        monkeypatch.setattr(settings, name, value)
    reset_engines()  # rebuilt with the tiny pools; disposed by the autouse fixture
    return float(settings.db_pool_timeout_seconds)


@pytest.mark.asyncio
async def test_concurrent_failed_logins_do_not_exhaust_the_pool(
    client: AsyncClient, tiny_pools: float
) -> None:
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})

    started = time.monotonic()
    responses = await asyncio.gather(
        *(client.post("/auth/login", json={"email": email, "password": WRONG}) for _ in range(8))
    )
    elapsed = time.monotonic() - started

    assert [r.status_code for r in responses] == [401] * 8
    assert elapsed < tiny_pools  # nobody sat out a pool timeout
    user = await _user(email)
    assert user.failed_login_count == 8  # atomic increments, none lost
    assert await _audit_count(AuditAction.LOGIN_FAILURE, user.id) == 8


@pytest.mark.asyncio
@pytest.mark.usefixtures("widen_rotation_race")
async def test_concurrent_refreshes_and_failed_logins_share_tiny_pools(
    client: AsyncClient, tiny_pools: float
) -> None:
    victim = _email()
    await client.post("/auth/register", json={"email": victim, "password": PASSWORD})
    owner = _email()
    await _register_and_login(client, owner)
    t1, csrf = _session_cookies(client)

    started = time.monotonic()
    refreshes = [_refresh_with(t1, csrf) for _ in range(6)]
    logins = [
        client.post("/auth/login", json={"email": victim, "password": WRONG}) for _ in range(4)
    ]
    responses = await asyncio.gather(*refreshes, *logins)
    elapsed = time.monotonic() - started

    assert sorted(r.status_code for r in responses[:6]) == [200, 409, 409, 409, 409, 409]
    assert [r.status_code for r in responses[6:]] == [401] * 4
    assert elapsed < tiny_pools
    assert (await _user(victim)).failed_login_count == 4
    assert await _live_tokens((await _user(owner)).id) == 1
