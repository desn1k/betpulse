"""F13 part B: an idempotent replay of a just-rotated refresh token.

After T1 → T2, presenting T1 again within ``REFRESH_REPLAY_WINDOW_SECONDS``
(from the same user-agent and client subnet, while T2 is unused) returns the
same T2 and a new access token; no refresh token is issued. Everything else
follows the rotation rules that predate it: 409 inside the reuse grace window,
family revocation after.

Time is never slept: T2's ``created_at`` is moved back on the database clock,
which is the clock both windows are measured on.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from app.core.config import Settings, get_settings
from app.core.db import _write_sessionmaker
from app.core.deps import get_redis_dep
from app.core.redis import get_redis
from app.core.security import hash_token
from app.models.audit_log import AuditLog
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.audit import AuditAction
from httpx import AsyncClient, Response
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select, text, update

PASSWORD = "correct horse battery staple"
UA = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"
OTHER_UA = "Mozilla/5.0 (Macintosh) Safari/26.0"
IP = "198.51.100.7"
REPLAY_KEY_PREFIX = "auth:refresh_replay:"


class _Session:
    """One signed-in browser: its user, the CSRF cookie and the token it held
    before the first rotation (T1)."""

    def __init__(self, user_id: uuid.UUID, t1: str, csrf: str) -> None:
        self.user_id = user_id
        self.t1 = t1
        self.csrf = csrf


async def _sign_in(client: AsyncClient) -> _Session:
    settings = get_settings()
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    async with _write_sessionmaker()() as s:
        user_id = await s.scalar(select(User.id).where(User.email == email))
    assert user_id is not None
    return _Session(
        user_id,
        client.cookies[settings.refresh_cookie_name],
        client.cookies[settings.csrf_cookie_name],
    )


async def _refresh(
    client: AsyncClient,
    token: str,
    csrf: str,
    *,
    ua: str | None = UA,
    ip: str = IP,
) -> Response:
    """POST /auth/refresh with exactly this cookie, user-agent and client IP
    (the test peer is loopback, a trusted proxy, so X-Forwarded-For is used)."""
    settings = get_settings()
    client.cookies.clear()
    client.cookies.set(settings.refresh_cookie_name, token)
    client.cookies.set(settings.csrf_cookie_name, csrf)
    headers = {settings.csrf_header_name: csrf, "X-Forwarded-For": ip}
    if ua is None:
        # httpx sends its own user-agent unless told otherwise.
        headers["User-Agent"] = ""
    else:
        headers["User-Agent"] = ua
    return await client.post("/auth/refresh", headers=headers)


def _set_refresh(response: Response) -> str | None:
    """The refresh token a response sets, if any."""
    name = get_settings().refresh_cookie_name
    for line in response.headers.get_list("set-cookie"):
        key, _, rest = line.partition("=")
        if key == name:
            value = rest.split(";", 1)[0]
            return value or None
    return None


async def _rotate(client: AsyncClient, session: _Session, **kwargs: Any) -> str:
    """T1 → T2 from the given user-agent and IP; returns T2."""
    r = await _refresh(client, session.t1, session.csrf, **kwargs)
    assert r.status_code == 200, r.text
    t2 = _set_refresh(r)
    assert t2 is not None and t2 != session.t1
    return t2


async def _age(token: str, seconds: float) -> None:
    """Move ``token``'s creation ``seconds`` into the past on the DB clock."""
    async with _write_sessionmaker()() as s:
        await s.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == hash_token(token))
            .values(created_at=func.clock_timestamp() - text(f"interval '{seconds} seconds'"))
        )
        await s.commit()


async def _token_rows(user_id: uuid.UUID) -> int:
    async with _write_sessionmaker()() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.user_id == user_id)
            )
            or 0
        )


async def _live_tokens(user_id: uuid.UUID) -> int:
    async with _write_sessionmaker()() as s:
        return int(
            await s.scalar(
                select(func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
            )
            or 0
        )


async def _audit(action: str) -> list[AuditLog]:
    async with _write_sessionmaker()() as s:
        return list(
            (
                await s.scalars(
                    select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
                )
            ).all()
        )


async def _redis(script: str) -> Any:
    """Run one Lua line against the test Redis (the hash commands are
    loosely typed in redis-py)."""
    return await get_redis().register_script(script)(keys=[], args=[])


async def _assert_replayed(response: Response, t2: str) -> None:
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]
    assert _set_refresh(response) == t2


async def _assert_refused(response: Response, *, status: int, reason: str) -> None:
    """Today's logic ran, and the refusal reason is on its audit row."""
    assert response.status_code == status, response.text
    action = (
        AuditAction.TOKEN_REFRESH_CONFLICT if status == 409 else AuditAction.TOKEN_REUSE_DETECTED
    )
    rows = await _audit(action)
    assert rows, f"no {action} audit row"
    assert rows[-1].meta.get("replay") == reason, rows[-1].meta


@pytest_asyncio.fixture
async def settings() -> AsyncIterator[Settings]:
    yield get_settings()


# --- the replay itself ------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_after_the_grace_window_returns_the_same_t2(client: AsyncClient) -> None:
    """The stand's sequence (F13): the rotation's answer never landed and the
    browser presents T1 again 11 s later. Today that revokes the family."""
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 11)
    rows_before = await _token_rows(s.user_id)

    r = await _refresh(client, s.t1, s.csrf)

    await _assert_replayed(r, t2)
    assert await _token_rows(s.user_id) == rows_before  # no new refresh token
    replayed = await _audit(AuditAction.TOKEN_REFRESH_REPLAYED)
    assert len(replayed) == 1
    assert replayed[0].actor_user_id == s.user_id
    assert replayed[0].meta["uses"] == 1
    assert 10 <= replayed[0].meta["age_seconds"] <= 12
    assert await _audit(AuditAction.TOKEN_REUSE_DETECTED) == []
    # The family is intact: T2 rotates normally afterwards.
    r = await _refresh(client, t2, s.csrf)
    assert r.status_code == 200, r.text
    assert _set_refresh(r) not in (None, t2)


@pytest.mark.asyncio
async def test_replay_inside_the_grace_window_returns_t2_instead_of_409(
    client: AsyncClient,
) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 5)

    await _assert_replayed(await _refresh(client, s.t1, s.csrf), t2)
    assert await _audit(AuditAction.TOKEN_REFRESH_CONFLICT) == []


@pytest.mark.asyncio
async def test_replay_is_capped_per_token(client: AsyncClient, settings: Settings) -> None:
    """At most REFRESH_REPLAY_MAX_USES successful replays of one T1; each returns
    the same T2 and issues nothing; the next one falls through."""
    assert settings.refresh_replay_max_uses == 3
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 11)
    rows_before = await _token_rows(s.user_id)

    for _ in range(3):
        await _assert_replayed(await _refresh(client, s.t1, s.csrf), t2)
    assert await _token_rows(s.user_id) == rows_before
    assert [row.meta["uses"] for row in await _audit(AuditAction.TOKEN_REFRESH_REPLAYED)] == [
        1,
        2,
        3,
    ]

    await _assert_refused(await _refresh(client, s.t1, s.csrf), status=401, reason="cap_reached")
    assert await _live_tokens(s.user_id) == 0


@pytest.mark.asyncio
async def test_concurrent_replays_never_exceed_the_cap(client: AsyncClient) -> None:
    from app.main import app
    from httpx import ASGITransport

    settings = get_settings()
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 5)  # inside the grace window: a refused replay is a harmless 409

    async def one() -> Response:
        cookies = {settings.refresh_cookie_name: s.t1, settings.csrf_cookie_name: s.csrf}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as c:
            return await c.post(
                "/auth/refresh",
                headers={
                    settings.csrf_header_name: s.csrf,
                    "X-Forwarded-For": IP,
                    "User-Agent": UA,
                },
            )

    responses = await asyncio.gather(*(one() for _ in range(6)))

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 409, 409, 409]
    assert {_set_refresh(r) for r in responses if r.status_code == 200} == {t2}


# --- the time windows, both sides of each boundary ---------------------------


@pytest.mark.asyncio
async def test_replay_window_boundary_inside(client: AsyncClient) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 59)
    await _assert_replayed(await _refresh(client, s.t1, s.csrf), t2)


@pytest.mark.asyncio
async def test_replay_window_boundary_passed(client: AsyncClient) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 61)

    await _assert_refused(await _refresh(client, s.t1, s.csrf), status=401, reason="window_passed")
    assert await _live_tokens(s.user_id) == 0


@pytest.mark.asyncio
async def test_refused_replay_inside_grace_is_still_409(client: AsyncClient) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 9)

    await _assert_refused(
        await _refresh(client, s.t1, s.csrf, ua=OTHER_UA), status=409, reason="ua_mismatch"
    )
    assert await _live_tokens(s.user_id) == 1


@pytest.mark.asyncio
async def test_refused_replay_after_grace_revokes_the_family(client: AsyncClient) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 11)

    await _assert_refused(
        await _refresh(client, s.t1, s.csrf, ua=OTHER_UA), status=401, reason="ua_mismatch"
    )
    assert await _live_tokens(s.user_id) == 0


# --- T2's state ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_replay_once_t2_was_used(client: AsyncClient) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    r = await _refresh(client, t2, s.csrf)
    assert r.status_code == 200, r.text

    await _assert_refused(await _refresh(client, s.t1, s.csrf), status=401, reason="t2_used")
    assert await _live_tokens(s.user_id) == 0


@pytest.mark.asyncio
async def test_no_replay_once_t2_was_revoked(client: AsyncClient) -> None:
    settings = get_settings()
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    client.cookies.clear()
    client.cookies.set(settings.refresh_cookie_name, t2)
    client.cookies.set(settings.csrf_cookie_name, s.csrf)
    r = await client.post("/auth/logout", headers={settings.csrf_header_name: s.csrf})
    assert r.status_code == 200, r.text

    await _assert_refused(await _refresh(client, s.t1, s.csrf), status=401, reason="t2_revoked")


# --- the binding to the rotating request ----------------------------------------


@pytest.mark.asyncio
async def test_no_replay_without_a_user_agent(client: AsyncClient) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 11)

    await _assert_refused(
        await _refresh(client, s.t1, s.csrf, ua=None), status=401, reason="ua_mismatch"
    )


@pytest.mark.asyncio
async def test_no_rotation_entry_without_a_user_agent(client: AsyncClient) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s, ua=None)
    await _age(t2, 11)

    await _assert_refused(
        await _refresh(client, s.t1, s.csrf, ua=None), status=401, reason="no_entry"
    )


@pytest.mark.parametrize(
    ("rotated_from", "replayed_from", "replayed"),
    [
        ("198.51.100.7", "198.51.100.200", True),  # same IPv4 /24
        ("198.51.100.7", "198.51.101.7", False),  # other /24
        ("2001:db8:1:1::5", "2001:db8:1:1:ffff::9", True),  # same IPv6 /64
        ("2001:db8:1:1::5", "2001:db8:1:2::5", False),  # other /64
        ("198.51.100.7", "2001:db8:1:1::5", False),  # other family
    ],
)
@pytest.mark.asyncio
async def test_replay_is_bound_to_the_client_subnet(
    client: AsyncClient, rotated_from: str, replayed_from: str, replayed: bool
) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s, ip=rotated_from)
    await _age(t2, 11)

    r = await _refresh(client, s.t1, s.csrf, ip=replayed_from)

    if replayed:
        await _assert_replayed(r, t2)
    else:
        await _assert_refused(r, status=401, reason="subnet_mismatch")


# --- the account -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_replay_for_a_disabled_user(client: AsyncClient) -> None:
    """Deactivated without the token revocation an admin disable also does, so
    the replay's own check is what refuses."""
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 11)
    async with _write_sessionmaker()() as db:
        await db.execute(update(User).where(User.id == s.user_id).values(is_active=False))
        await db.commit()

    await _assert_refused(await _refresh(client, s.t1, s.csrf), status=401, reason="user_inactive")


@pytest.mark.asyncio
async def test_no_replay_after_the_credentials_changed(client: AsyncClient) -> None:
    """``credentials_changed_at`` bumped without revoking anything: the replay's
    own check refuses (every real bump also revokes T2)."""
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 11)
    async with _write_sessionmaker()() as db:
        await db.execute(
            update(User)
            .where(User.id == s.user_id)
            .values(credentials_changed_at=func.clock_timestamp())
        )
        await db.commit()

    await _assert_refused(
        await _refresh(client, s.t1, s.csrf), status=401, reason="credentials_changed"
    )


@pytest.mark.asyncio
async def test_no_replay_after_a_password_change(client: AsyncClient) -> None:
    s = await _sign_in(client)
    r = await _refresh(client, s.t1, s.csrf)
    assert r.status_code == 200, r.text
    access = r.json()["access_token"]
    t2 = _set_refresh(r)
    assert t2 is not None
    r = await client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {access}"},
        json={"current_password": PASSWORD, "new_password": "a-brand-new-pass-9"},
    )
    assert r.status_code == 200, r.text

    await _assert_refused(await _refresh(client, s.t1, s.csrf), status=401, reason="t2_revoked")


# --- the per-IP replay limit --------------------------------------------------------


@pytest.mark.asyncio
async def test_replays_are_limited_per_client_ip(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful replays count per client IP (IPv6 per /64); over the limit a
    replay falls through to today's logic, never a 429."""
    monkeypatch.setattr(settings, "rate_limit_refresh_replay_per_minute", 1)
    other_ip = "203.0.113.9"
    first = await _sign_in(client)
    second = await _sign_in(client)
    third = await _sign_in(client)
    t2_first = await _rotate(client, first)
    t2_second = await _rotate(client, second)
    t2_third = await _rotate(client, third, ip=other_ip)
    for t2 in (t2_first, t2_second, t2_third):
        await _age(t2, 5)

    await _assert_replayed(await _refresh(client, first.t1, first.csrf), t2_first)
    await _assert_refused(
        await _refresh(client, second.t1, second.csrf), status=409, reason="ip_limited"
    )
    # Another address has its own budget.
    await _assert_replayed(await _refresh(client, third.t1, third.csrf, ip=other_ip), t2_third)


@pytest.mark.asyncio
async def test_replay_limit_counts_only_successful_replays(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_refresh_replay_per_minute", 1)
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 5)

    # A refused replay (other user-agent) does not spend the address's budget.
    await _assert_refused(
        await _refresh(client, s.t1, s.csrf, ua=OTHER_UA), status=409, reason="ua_mismatch"
    )
    await _assert_replayed(await _refresh(client, s.t1, s.csrf), t2)


# --- the store ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_is_encrypted_and_expires_with_the_window(client: AsyncClient) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s)

    redis = get_redis()
    key = REPLAY_KEY_PREFIX + hash_token(s.t1)
    stored = await _redis(f"return redis.call('HGETALL', '{key}')")
    assert stored, "no replay entry after a rotation"
    assert t2 not in str(stored)
    assert 0 < await redis.ttl(key) <= 60


@pytest.mark.asyncio
async def test_tampered_entry_is_refused(client: AsyncClient) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 11)
    key = REPLAY_KEY_PREFIX + hash_token(s.t1)
    await _redis(f"return redis.call('HSET', '{key}', 'data', 'not-a-fernet-token')")

    await _assert_refused(await _refresh(client, s.t1, s.csrf), status=401, reason="entry_invalid")


class _DeadRedis:
    """Every command fails as if Redis were down."""

    def __getattr__(self, name: str) -> Any:
        async def fail(*args: Any, **kwargs: Any) -> Any:
            raise RedisConnectionError("redis is down")

        return fail

    def register_script(self, script: str) -> Any:
        async def fail(*args: Any, **kwargs: Any) -> Any:
            raise RedisConnectionError("redis is down")

        return fail


class _HungRedis:
    """Every command hangs."""

    def __getattr__(self, name: str) -> Any:
        async def hang(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(30)

        return hang

    def register_script(self, script: str) -> Any:
        async def hang(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(30)

        return hang


@pytest.mark.asyncio
async def test_rotation_succeeds_when_redis_is_down(client: AsyncClient) -> None:
    from app.main import app

    s = await _sign_in(client)
    app.dependency_overrides[get_redis_dep] = _DeadRedis
    try:
        t2 = await _rotate(client, s)
    finally:
        app.dependency_overrides.pop(get_redis_dep, None)
    await _age(t2, 11)

    # Nothing was remembered, so the replay falls through to today's logic.
    await _assert_refused(await _refresh(client, s.t1, s.csrf), status=401, reason="no_entry")


@pytest.mark.parametrize("broken", [_DeadRedis, _HungRedis])
@pytest.mark.asyncio
async def test_replay_falls_through_when_redis_is_unavailable(
    client: AsyncClient, broken: type
) -> None:
    from app.main import app

    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 5)
    app.dependency_overrides[get_redis_dep] = broken
    try:
        started = time.monotonic()
        r = await _refresh(client, s.t1, s.csrf)
        elapsed = time.monotonic() - started
    finally:
        app.dependency_overrides.pop(get_redis_dep, None)

    await _assert_refused(r, status=409, reason="store_unavailable")
    assert elapsed < 3, f"a hung Redis held the refresh for {elapsed:.1f} s"


@pytest.mark.asyncio
async def test_window_zero_turns_the_replay_off(
    client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "refresh_replay_window_seconds", 0)
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 11)

    key = REPLAY_KEY_PREFIX + hash_token(s.t1)
    assert await _redis(f"return redis.call('EXISTS', '{key}')") == 0
    r = await _refresh(client, s.t1, s.csrf)
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_audit_rows_never_carry_tokens(client: AsyncClient) -> None:
    s = await _sign_in(client)
    t2 = await _rotate(client, s)
    await _age(t2, 11)
    await _assert_replayed(await _refresh(client, s.t1, s.csrf), t2)
    await _refresh(client, s.t1, s.csrf, ua=OTHER_UA)

    secrets = {s.t1, t2, hash_token(s.t1), hash_token(t2)}
    async with _write_sessionmaker()() as db:
        rows = (await db.scalars(select(AuditLog))).all()
    for row in rows:
        blob = f"{row.target} {row.meta}"
        assert not any(secret in blob for secret in secrets), row.action


# --- settings and the subnet helper ----------------------------------------------


@pytest.mark.parametrize(
    ("window", "ok"), [(0, True), (10, True), (60, True), (300, True), (9, False), (301, False)]
)
def test_replay_window_setting_bounds(
    monkeypatch: pytest.MonkeyPatch, window: int, ok: bool
) -> None:
    """0 turns the replay off; otherwise between the reuse grace window (10 s)
    and 300 s."""
    monkeypatch.setenv("REFRESH_REPLAY_WINDOW_SECONDS", str(window))
    if ok:
        assert Settings().refresh_replay_window_seconds == window
    else:
        with pytest.raises(ValueError, match="REFRESH_REPLAY_WINDOW_SECONDS"):
            Settings()


@pytest.mark.parametrize(
    ("ip", "subnet"),
    [
        ("198.51.100.7", "198.51.100.0/24"),
        ("2001:db8:1:1::5", "2001:db8:1:1::/64"),
        ("unknown", None),
        ("garbage", None),
    ],
)
def test_replay_subnet(ip: str, subnet: str | None) -> None:
    from app.core.client_ip import replay_subnet

    assert replay_subnet(ip) == subnet
