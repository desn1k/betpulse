"""Concurrency integrity of Redis counters and refresh-token revocation.

Run against real Redis and Postgres: the properties under test (atomic
INCR+EXPIRE, check-and-increment against a quota, revocation that cannot miss
a concurrently rotated token) only exist in the real servers.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from app.core.config import get_settings
from app.core.db import _write_sessionmaker
from app.core.redis import get_redis
from app.models.fixture import Fixture, FixtureStatus
from app.models.live import PushChannel, PushFollow, PushSubscription
from app.models.reference import League, Team
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserTier
from app.services import auth as auth_service
from app.services import limits
from app.services.live.push import dispatch_push
from app.services.rate_limit import enforce_fixed_window
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select

PASSWORD = "correct horse battery staple"
TELEGRAM_URL = "https://api.telegram.org/botTEST/sendMessage"


# --- b) INCR + EXPIRE is atomic: a counter never lives without a TTL --------


def _counter_calls(now: datetime) -> list[tuple[str, Callable[[], Awaitable[Any]]]]:
    redis = get_redis()
    user_id = uuid.UUID(int=1)
    return [
        (
            f"limits:ip-a:{now:%Y-%m-%d}",
            lambda: limits.consume_match_view(
                redis, identity="ip-a", fixture_id=uuid.UUID(int=7), limit=50, now=now
            ),
        ),
        (
            f"limits:seen:ip-b:{now:%Y-%m-%d}",
            lambda: limits.consume_match_view(
                redis, identity="ip-b", fixture_id=uuid.UUID(int=7), limit=50, now=now
            ),
        ),
        (
            f"limits:backtester:{user_id}:{now:%Y-%m-%d}",
            lambda: limits.consume_backtester_run(redis, user_id=user_id, limit=50, now=now),
        ),
        (
            f"rate_limit:promo:{user_id}:{now:%Y-%m-%d-%H}",
            lambda: limits.enforce_promo_redeem_limit(redis, user_id=user_id, limit=50, now=now),
        ),
        (
            f"limits:push:{user_id}:{now:%Y-%m-%d}",
            lambda: limits.record_push_delivered(redis, user_id=user_id, now=now),
        ),
        (
            "rl:login:ip:test-bucket",
            lambda: enforce_fixed_window(
                redis, key="rl:login:ip:test-bucket", limit=50, window_seconds=60
            ),
        ),
    ]


@pytest.mark.asyncio
async def test_every_counter_sets_a_ttl() -> None:
    redis = get_redis()
    for key, call in _counter_calls(datetime.now(UTC)):
        await call()
        assert await redis.ttl(key) > 0, key


@pytest.mark.asyncio
async def test_counter_left_without_ttl_is_healed() -> None:
    """A process that died between INCR and EXPIRE left a key with no TTL. For
    the un-bucketed login key that is a permanent per-IP lockout; for the dated
    keys a leak. The next increment must give it a TTL."""
    redis = get_redis()
    for key, call in _counter_calls(datetime.now(UTC)):
        # The crash leftover: counted (or marked seen), never expired.
        if key.startswith("limits:seen:"):
            await redis.sadd(key, str(uuid.UUID(int=7)))
        else:
            await redis.set(key, 1)
        assert await redis.ttl(key) == -1
        await call()
        assert await redis.ttl(key) > 0, key


# --- c) quotas: check-and-increment never exceeds, release never negative --


@pytest.mark.asyncio
async def test_parallel_match_views_never_exceed_the_quota() -> None:
    redis = get_redis()
    now = datetime.now(UTC)
    results = await asyncio.gather(
        *(
            limits.consume_match_view(
                redis, identity="race", fixture_id=uuid.UUID(int=i), limit=5, now=now
            )
            for i in range(40)
        ),
        return_exceptions=True,
    )
    granted = [r for r in results if not isinstance(r, BaseException)]
    assert len(granted) == 5
    assert sorted(granted) == [0, 1, 2, 3, 4]
    assert all(isinstance(r, limits.LimitExceeded) for r in results if r not in granted)
    assert int(await redis.get(f"limits:race:{now:%Y-%m-%d}")) == 5
    assert await redis.scard(f"limits:seen:race:{now:%Y-%m-%d}") == 5


@pytest.mark.asyncio
async def test_parallel_views_of_one_match_cost_one_view() -> None:
    """F6: several tabs (and their 60 s refetches) opening the same new match at
    once take exactly one unit of the day's budget."""
    redis = get_redis()
    now = datetime.now(UTC)
    match = uuid.uuid4()
    results = await asyncio.gather(
        *(
            limits.consume_match_view(redis, identity="tabs", fixture_id=match, limit=3, now=now)
            for _ in range(30)
        )
    )
    assert results == [2] * 30
    assert int(await redis.get(f"limits:tabs:{now:%Y-%m-%d}")) == 1
    assert await redis.smembers(f"limits:seen:tabs:{now:%Y-%m-%d}") == {str(match)}


@pytest.mark.asyncio
async def test_parallel_backtester_runs_never_exceed_the_quota() -> None:
    redis = get_redis()
    user_id = uuid.uuid4()
    results = await asyncio.gather(
        *(limits.consume_backtester_run(redis, user_id=user_id, limit=3) for _ in range(30)),
        return_exceptions=True,
    )
    assert sum(1 for r in results if r is None) == 3
    assert int(await redis.get(f"limits:backtester:{user_id}:{datetime.now(UTC):%Y-%m-%d}")) == 3


async def _seed_push_user(follows: int) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A Pro user (10 pushes/day) subscribed via Telegram, following ``follows``
    live fixtures. Committed, so concurrent dispatches in their own sessions
    see it."""
    async with _write_sessionmaker()() as s:
        user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=UserTier.pro)
        league = League(code=f"L{uuid.uuid4().hex[:4]}", name="League")
        home = Team(name="H", normalized_name=f"h-{uuid.uuid4().hex[:6]}")
        away = Team(name="A", normalized_name=f"a-{uuid.uuid4().hex[:6]}")
        s.add_all([user, league, home, away])
        await s.flush()
        s.add(PushSubscription(user_id=user.id, channel=PushChannel.telegram, endpoint="12345"))
        fixture_ids = []
        for _ in range(follows):
            fixture = Fixture(
                league_id=league.id,
                season="2025-2026",
                home_team_id=home.id,
                away_team_id=away.id,
                kickoff_at=datetime.now(UTC) + timedelta(hours=2),
                status=FixtureStatus.live,
            )
            s.add(fixture)
            await s.flush()
            s.add(PushFollow(user_id=user.id, fixture_id=fixture.id))
            fixture_ids.append(fixture.id)
        await s.commit()
        return user.id, fixture_ids


@pytest.mark.asyncio
@respx.mock
async def test_parallel_push_dispatches_never_overspend_the_daily_budget() -> None:
    """Swing pushes for different fixtures run as parallel realtime jobs. With
    one push left in the budget, exactly one may be delivered."""

    async def slow_telegram(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)  # delivery takes a while — the race window
        return httpx.Response(200)

    route = respx.post(TELEGRAM_URL).mock(side_effect=slow_telegram)
    user_id, fixture_ids = await _seed_push_user(follows=5)
    redis = get_redis()
    key = f"limits:push:{user_id}:{datetime.now(UTC):%Y-%m-%d}"
    await redis.set(key, 9, ex=3600)  # Pro = 10/day → one push left

    settings = get_settings().model_copy(
        update={
            "telegram_bot_token": "TEST",
            "telegram_api_base_url": "https://api.telegram.org",
            "push_rate_limit_seconds": 300,
            "push_retry_delay_seconds": 0,
        }
    )

    async def dispatch(fixture_id: uuid.UUID) -> int:
        async with _write_sessionmaker()() as s:
            result = await dispatch_push(
                s, redis, fixture_id=fixture_id, text="swing", settings=settings
            )
            await s.commit()
            return result.delivered

    delivered = await asyncio.gather(*(dispatch(f) for f in fixture_ids))

    assert sum(delivered) == 1
    assert route.call_count == 1
    assert int(await redis.get(key)) == 10


@pytest.mark.asyncio
@respx.mock
async def test_failed_push_delivery_releases_its_reservation() -> None:
    respx.post(TELEGRAM_URL).mock(return_value=httpx.Response(500))
    user_id, (fixture_id,) = await _seed_push_user(follows=1)
    redis = get_redis()
    settings = get_settings().model_copy(
        update={
            "telegram_bot_token": "TEST",
            "telegram_api_base_url": "https://api.telegram.org",
            "push_retry_delay_seconds": 0,
        }
    )
    async with _write_sessionmaker()() as s:
        result = await dispatch_push(s, redis, fixture_id=fixture_id, text="x", settings=settings)
    assert result.delivered == 0
    used = await redis.get(f"limits:push:{user_id}:{datetime.now(UTC):%Y-%m-%d}")
    assert int(used or 0) == 0  # a failed delivery never consumes the budget


# --- a) password change vs. concurrent rotation ---------------------------


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


@pytest.mark.asyncio
async def test_password_change_revokes_a_concurrently_rotated_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refresh holds its token family locked while it rotates. A password
    change landing in that window must still leave no live token behind — not
    the rotated one, and not the descendant the rotation is about to commit."""
    settings = get_settings()
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    await client.post("/auth/register", json={"email": email, "password": PASSWORD})
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    access = login.json()["access_token"]
    t1 = client.cookies[settings.refresh_cookie_name]
    csrf = client.cookies[settings.csrf_cookie_name]
    async with _write_sessionmaker()() as s:
        user_id = (await s.scalar(select(User.id).where(User.email == email))) or uuid.uuid4()

    rotation_locked = asyncio.Event()
    original = auth_service.get_user_by_id

    async def held_open(*args: Any, **kwargs: Any) -> User | None:
        # Called by the rotation with the family lock and the row lock held.
        rotation_locked.set()
        await asyncio.sleep(0.5)  # the password change runs meanwhile
        return await original(*args, **kwargs)

    monkeypatch.setattr(auth_service, "get_user_by_id", held_open)

    from app.main import app

    async def refresh() -> Response:
        cookies = {settings.refresh_cookie_name: t1, settings.csrf_cookie_name: csrf}
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as c:
            return await c.post(
                settings.refresh_cookie_path, headers={settings.csrf_header_name: csrf}
            )

    async def change_password() -> Response:
        await rotation_locked.wait()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post(
                "/auth/change-password",
                headers={"Authorization": f"Bearer {access}"},
                json={"current_password": PASSWORD, "new_password": "a-brand-new-pass-9"},
            )

    refreshed, changed = await asyncio.gather(refresh(), change_password())

    assert refreshed.status_code == 200, refreshed.text
    assert changed.status_code == 200, changed.text
    assert await _live_tokens(user_id) == 0


@pytest.mark.asyncio
async def test_admin_disable_revokes_a_concurrently_rotated_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import user_admin

    async with _write_sessionmaker()() as s:
        user = await auth_service.register_user(s, email=f"{uuid.uuid4()}@x.com", password=PASSWORD)
        tokens = await auth_service.issue_token_pair(s, user)
        await s.commit()
        user_id = user.id

    rotation_locked = asyncio.Event()
    original = auth_service.get_user_by_id

    async def held_open(*args: Any, **kwargs: Any) -> User | None:
        rotation_locked.set()
        await asyncio.sleep(0.5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(auth_service, "get_user_by_id", held_open)

    async def disable() -> int:
        await rotation_locked.wait()
        async with _write_sessionmaker()() as s:
            target = await s.get(User, user_id)
            assert target is not None
            revoked = await user_admin.disable_user(s, user=target)
            await s.commit()
            return revoked

    rotated, revoked = await asyncio.gather(
        auth_service.rotate_refresh_token(refresh_token=tokens.refresh_token), disable()
    )

    assert rotated.refresh_token
    assert revoked == 1  # the descendant the rotation committed while we waited
    assert await _live_tokens(user_id) == 0


@pytest.mark.asyncio
async def test_parallel_push_reservations_never_exceed_the_limit() -> None:
    redis = get_redis()
    user_id = uuid.uuid4()
    granted = await asyncio.gather(
        *(limits.reserve_push(redis, user_id=user_id, limit=3) for _ in range(25))
    )
    assert granted.count(True) == 3
    key = f"limits:push:{user_id}:{datetime.now(UTC):%Y-%m-%d}"
    assert int(await redis.get(key)) == 3
    assert await redis.ttl(key) > 0
    assert await limits.reserve_push(redis, user_id=user_id, limit=0) is False


@pytest.mark.asyncio
async def test_release_never_goes_negative() -> None:
    redis = get_redis()
    user_id = uuid.uuid4()
    key = f"limits:push:{user_id}:{datetime.now(UTC):%Y-%m-%d}"
    assert await limits.reserve_push(redis, user_id=user_id, limit=5)
    # More releases than reservations, concurrently: the count bottoms out at 0.
    await asyncio.gather(*(limits.release_push(redis, user_id=user_id) for _ in range(10)))
    assert int(await redis.get(key)) == 0
    # A release with no key at all creates nothing.
    other = uuid.uuid4()
    await limits.release_push(redis, user_id=other)
    assert await redis.get(f"limits:push:{other}:{datetime.now(UTC):%Y-%m-%d}") is None
