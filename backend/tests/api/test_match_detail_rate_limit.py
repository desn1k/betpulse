"""Per-caller request limit on GET /matches/{id}, checked before the fixture
lookup (CodeRabbit on ER-M-05): unknown ids no longer spend the daily view
quota, so without it they would reach the database without any limit.

The identity is the daily quota's: the user id, or the guest's IP (IPv6 /64).
The default test client is a trusted proxy (127.0.0.1), so X-Forwarded-For
picks the guest.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from app.core.config import get_settings
from app.core.deps import get_settings_dep
from app.services import counters
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.api.test_tiers import _seed_match

DEFAULT_LIMIT = 120  # requests per 60 s
GUEST_DAILY_VIEWS = 3


def _guest(ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip}


async def _detail(client: AsyncClient, match_id: uuid.UUID | str, ip: str) -> int:
    return (await client.get(f"/matches/{match_id}", headers=_guest(ip))).status_code


def test_default_limit_is_120_per_60_seconds() -> None:
    settings = get_settings()
    assert settings.rate_limit_match_detail_per_window == DEFAULT_LIMIT
    assert settings.rate_limit_match_detail_window_seconds == 60


async def test_121st_request_for_unknown_ids_gets_429(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_match(session)
    await session.commit()
    ip = "198.51.100.20"
    for _ in range(DEFAULT_LIMIT):
        assert await _detail(client, uuid.uuid4(), ip) == 404
    limited = await client.get(f"/matches/{uuid.uuid4()}", headers=_guest(ip))
    assert limited.status_code == 429
    assert 0 < int(limited.headers["Retry-After"]) <= 60
    # The 404s never spent the daily view quota (ER-M-05).
    listing = await client.get("/matches", headers=_guest(ip))
    assert listing.json()["matches_remaining"] == GUEST_DAILY_VIEWS


async def test_121st_request_for_an_existing_match_gets_429(
    client: AsyncClient, session: AsyncSession
) -> None:
    fixture = await _seed_match(session)
    await session.commit()
    ip = "198.51.100.21"
    codes = [await _detail(client, fixture.id, ip) for _ in range(DEFAULT_LIMIT)]
    # The daily quota is unchanged: three views, then 403 while under the limit.
    assert codes[:GUEST_DAILY_VIEWS] == [200] * GUEST_DAILY_VIEWS
    assert set(codes[GUEST_DAILY_VIEWS:]) == {403}
    assert await _detail(client, fixture.id, ip) == 429


async def test_the_limit_is_per_identity(client: AsyncClient, session: AsyncSession) -> None:
    fixture = await _seed_match(session)
    await session.commit()
    for _ in range(DEFAULT_LIMIT + 1):
        await _detail(client, uuid.uuid4(), "198.51.100.22")
    assert await _detail(client, uuid.uuid4(), "198.51.100.22") == 429
    # Another guest still gets through, quota intact.
    assert await _detail(client, uuid.uuid4(), "198.51.100.23") == 404
    assert await _detail(client, fixture.id, "198.51.100.23") == 200


@pytest.fixture
def small_limit() -> Iterator[None]:
    from app.main import app

    settings = get_settings().model_copy(
        update={
            "rate_limit_match_detail_per_window": 5,
            "rate_limit_match_detail_window_seconds": 30,
        }
    )
    app.dependency_overrides[get_settings_dep] = lambda: settings
    yield
    app.dependency_overrides.pop(get_settings_dep, None)


async def test_threshold_and_window_come_from_settings(
    client: AsyncClient, session: AsyncSession, small_limit: None
) -> None:
    ip = "198.51.100.24"
    for _ in range(5):
        assert await _detail(client, uuid.uuid4(), ip) == 404
    limited = await client.get(f"/matches/{uuid.uuid4()}", headers=_guest(ip))
    assert limited.status_code == 429
    assert 0 < int(limited.headers["Retry-After"]) <= 30


async def test_redis_outage_fails_closed_like_analysis(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # /matches/{id}/analysis lets a Redis error propagate (no fallback): the
    # request fails rather than running unlimited. The detail limit does the same.
    async def _down(*_args: object, **_kwargs: object) -> int:
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(counters, "incr_with_ttl", _down)
    from app.services import rate_limit

    monkeypatch.setattr(rate_limit, "incr_with_ttl", _down)
    for path in (f"/matches/{uuid.uuid4()}", f"/matches/{uuid.uuid4()}/analysis"):
        with pytest.raises(RedisConnectionError):
            await client.get(path, headers=_guest("198.51.100.25"))
