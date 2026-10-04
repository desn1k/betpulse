"""Push dispatch: followers-only targeting, per-user window + daily budget,
dead-endpoint pruning, one-retry-then-discard, and VAPID signing (Phase 5 + 11)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from app.core.config import Settings, get_settings
from app.core.redis import get_redis
from app.models.fixture import Fixture, FixtureStatus
from app.models.live import PushChannel, PushFollow, PushSubscription
from app.models.reference import League, Team
from app.models.user import User, UserTier
from app.services.live.push import (
    PushError,
    _b64url_decode,
    build_vapid_jwt,
    dispatch_push,
    send_webpush,
)
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

TELEGRAM_URL = "https://api.telegram.org/botTEST/sendMessage"

# A real-world Chrome endpoint shape, and the public address the fake DNS gives it.
FCM_ENDPOINT = "https://fcm.googleapis.com/fcm/send/dXJ0aWNsZQ:APA91bHqT0"
FCM_IP = "142.250.74.10"
FCM_PINNED = f"https://{FCM_IP}/fcm/send/dXJ0aWNsZQ:APA91bHqT0"


def _fake_dns(monkeypatch: pytest.MonkeyPatch, *answers: list[str]) -> list[str]:
    """Replace DNS for the SSRF guard; each call returns the next answer (the
    last one repeats). Returns the list of hosts looked up."""
    looked_up: list[str] = []
    queue = list(answers)

    async def fake(host: str) -> list[str]:
        looked_up.append(host)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr("app.services.live.push_endpoint._getaddrinfo", fake)
    return looked_up


def _vapid_settings(**overrides: object) -> Settings:
    priv = ec.generate_private_key(ec.SECP256R1())
    scalar = priv.private_numbers().private_value.to_bytes(32, "big")
    return _settings(
        webpush_vapid_private_key=base64.urlsafe_b64encode(scalar).rstrip(b"=").decode(),
        webpush_vapid_public_key="BPUBLICKEY",
        webpush_contact_email="admin@example.com",
        **overrides,
    )


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "telegram_bot_token": "TEST",
        "telegram_api_base_url": "https://api.telegram.org",
        "push_rate_limit_seconds": 300,
        "push_retry_delay_seconds": 0,
    }
    base.update(overrides)
    return get_settings().model_copy(update=base)


async def _make_user(session: AsyncSession, *, tier: UserTier = UserTier.pro) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=tier)
    session.add(user)
    await session.flush()
    return user.id


async def _make_fixture(session: AsyncSession) -> uuid.UUID:
    league = League(code=f"L{uuid.uuid4().hex[:4]}", name="League")
    home = Team(name="H", normalized_name=f"h-{uuid.uuid4().hex[:6]}")
    away = Team(name="A", normalized_name=f"a-{uuid.uuid4().hex[:6]}")
    session.add_all([league, home, away])
    await session.flush()
    fixture = Fixture(
        league_id=league.id,
        season="2025-2026",
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_at=datetime.now(UTC) + timedelta(hours=2),
        status=FixtureStatus.live,
    )
    session.add(fixture)
    await session.flush()
    return fixture.id


async def _follow(session: AsyncSession, user_id: uuid.UUID, fixture_id: uuid.UUID) -> None:
    session.add(PushFollow(user_id=user_id, fixture_id=fixture_id))
    await session.flush()


async def _noop_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_allows_one_push_per_window(session: AsyncSession) -> None:
    route = respx.post(TELEGRAM_URL).mock(return_value=httpx.Response(200))
    user_id = await _make_user(session)
    fixture_id = await _make_fixture(session)
    session.add(PushSubscription(user_id=user_id, channel=PushChannel.telegram, endpoint="12345"))
    await _follow(session, user_id, fixture_id)
    await session.flush()

    settings = _settings()
    redis = get_redis()
    first = await dispatch_push(session, redis, fixture_id=fixture_id, text="hi", settings=settings)
    second = await dispatch_push(
        session, redis, fixture_id=fixture_id, text="hi", settings=settings
    )

    assert first.delivered == 1 and first.rate_limited == 0
    assert second.rate_limited == 1 and second.delivered == 0
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_delivers_only_to_followers(session: AsyncSession) -> None:
    # Two subscribed users, but only one follows the fixture — only the follower
    # is notified. No spam to every subscriber.
    route = respx.post(TELEGRAM_URL).mock(return_value=httpx.Response(200))
    follower = await _make_user(session)
    bystander = await _make_user(session)
    fixture_id = await _make_fixture(session)
    session.add_all(
        [
            PushSubscription(user_id=follower, channel=PushChannel.telegram, endpoint="111"),
            PushSubscription(user_id=bystander, channel=PushChannel.telegram, endpoint="222"),
        ]
    )
    await _follow(session, follower, fixture_id)
    await session.flush()

    result = await dispatch_push(
        session, get_redis(), fixture_id=fixture_id, text="hi", settings=_settings()
    )
    assert result.delivered == 1
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_skips_when_no_follower_subscription(session: AsyncSession) -> None:
    result = await dispatch_push(
        session, get_redis(), fixture_id=uuid.uuid4(), text="hi", settings=_settings()
    )
    assert result.skipped_no_subscription is True
    assert result.delivered == 0


@pytest.mark.asyncio
@respx.mock
async def test_daily_budget_hard_stops_delivery(session: AsyncSession) -> None:
    route = respx.post(TELEGRAM_URL).mock(return_value=httpx.Response(200))
    user_id = await _make_user(session, tier=UserTier.pro)  # 10/day
    fixture_id = await _make_fixture(session)
    session.add(PushSubscription(user_id=user_id, channel=PushChannel.telegram, endpoint="12345"))
    await _follow(session, user_id, fixture_id)
    await session.flush()

    redis = get_redis()
    key = f"limits:push:{user_id}:{datetime.now(UTC):%Y-%m-%d}"
    await redis.set(key, 10)  # budget already spent

    result = await dispatch_push(
        session, redis, fixture_id=fixture_id, text="hi", settings=_settings()
    )
    assert result.budget_exhausted == 1
    assert result.delivered == 0
    assert route.call_count == 0  # never attempted


@pytest.mark.asyncio
@respx.mock
async def test_free_tier_receives_nothing(session: AsyncSession) -> None:
    # A stale follow from a since-downgraded free user (pushes_per_day == 0).
    route = respx.post(TELEGRAM_URL).mock(return_value=httpx.Response(200))
    user_id = await _make_user(session, tier=UserTier.free)
    fixture_id = await _make_fixture(session)
    session.add(PushSubscription(user_id=user_id, channel=PushChannel.telegram, endpoint="1"))
    await _follow(session, user_id, fixture_id)
    await session.flush()

    result = await dispatch_push(
        session, get_redis(), fixture_id=fixture_id, text="hi", settings=_settings()
    )
    assert result.delivered == 0
    assert result.budget_exhausted == 1
    assert route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_prunes_dead_webpush_endpoint(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = FCM_ENDPOINT
    _fake_dns(monkeypatch, [FCM_IP])
    respx.post(FCM_PINNED).mock(return_value=httpx.Response(410))  # Gone
    settings = _vapid_settings()
    user_id = await _make_user(session)
    fixture_id = await _make_fixture(session)
    session.add(PushSubscription(user_id=user_id, channel=PushChannel.webpush, endpoint=endpoint))
    await _follow(session, user_id, fixture_id)
    await session.flush()

    result = await dispatch_push(
        session, get_redis(), fixture_id=fixture_id, text="hi", settings=settings
    )
    await session.flush()

    assert result.pruned == 1
    assert result.delivered == 0
    remaining = (
        await session.execute(
            select(func.count())
            .select_from(PushSubscription)
            .where(PushSubscription.user_id == user_id)
        )
    ).scalar_one()
    assert remaining == 0


@pytest.mark.asyncio
@respx.mock
async def test_failure_retries_once_then_discards(session: AsyncSession) -> None:
    route = respx.post(TELEGRAM_URL).mock(return_value=httpx.Response(500))
    user_id = await _make_user(session)
    fixture_id = await _make_fixture(session)
    session.add(PushSubscription(user_id=user_id, channel=PushChannel.telegram, endpoint="12345"))
    await _follow(session, user_id, fixture_id)
    await session.flush()

    result = await dispatch_push(
        session,
        get_redis(),
        fixture_id=fixture_id,
        text="hi",
        settings=_settings(),
        sleep=_noop_sleep,
    )
    assert result.failed == 1
    assert result.delivered == 0
    assert route.call_count == 2  # original + exactly one retry


@pytest.mark.asyncio
@respx.mock
async def test_webpush_sends_vapid_authenticated_request_to_the_pinned_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request goes to the address the guard checked, yet still speaks to
    the push service by name (Host + TLS SNI) and carries the unchanged VAPID
    authentication and empty body."""
    looked_up = _fake_dns(monkeypatch, [FCM_IP])
    route = respx.post(FCM_PINNED).mock(return_value=httpx.Response(201))
    settings = _vapid_settings()

    await send_webpush(settings, FCM_ENDPOINT)

    assert looked_up == ["fcm.googleapis.com"]
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.url.host == FCM_IP
    assert request.headers["Host"] == "fcm.googleapis.com"
    assert request.extensions["sni_hostname"] == "fcm.googleapis.com"
    assert request.headers["TTL"] == "300"
    assert request.content == b""
    auth = request.headers["Authorization"]
    assert auth.startswith("vapid t=")
    assert auth.endswith(", k=BPUBLICKEY")
    token = auth.removeprefix("vapid t=").split(",")[0]
    claims = json.loads(_b64url_decode(token.split(".")[1]))
    # The audience is the push service's origin, never the pinned address.
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert claims["sub"] == "mailto:admin@example.com"


@pytest.mark.asyncio
@respx.mock
async def test_webpush_refuses_rebinding_to_a_private_address(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allowed at subscribe time, but DNS now answers a private address: nothing
    is sent (neither attempt), and the row is kept, since a broken resolver must
    not prune every subscription."""
    looked_up = _fake_dns(monkeypatch, ["10.0.0.5"])
    route = respx.route().mock(return_value=httpx.Response(201))
    user_id = await _make_user(session)
    fixture_id = await _make_fixture(session)
    subscription = PushSubscription(
        user_id=user_id, channel=PushChannel.webpush, endpoint=FCM_ENDPOINT
    )
    session.add(subscription)
    await _follow(session, user_id, fixture_id)
    await session.flush()

    result = await dispatch_push(
        session,
        get_redis(),
        fixture_id=fixture_id,
        text="hi",
        settings=_vapid_settings(),
        sleep=_noop_sleep,
    )
    await session.flush()

    assert looked_up == ["fcm.googleapis.com", "fcm.googleapis.com"]  # first try + retry
    assert route.call_count == 0
    assert result.pruned == 0
    assert result.failed == 1
    assert result.delivered == 0
    assert await session.get(PushSubscription, subscription.id) is not None


@pytest.mark.asyncio
@respx.mock
async def test_webpush_prunes_a_stored_endpoint_outside_the_allowlist(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row stored before the guard existed is refused at send time."""
    looked_up = _fake_dns(monkeypatch, [FCM_IP])
    route = respx.route().mock(return_value=httpx.Response(201))
    user_id = await _make_user(session)
    fixture_id = await _make_fixture(session)
    session.add(
        PushSubscription(
            user_id=user_id, channel=PushChannel.webpush, endpoint="http://mlflow:5000/api"
        )
    )
    await _follow(session, user_id, fixture_id)
    await session.flush()

    result = await dispatch_push(
        session, get_redis(), fixture_id=fixture_id, text="hi", settings=_vapid_settings()
    )

    assert looked_up == []  # refused before any DNS lookup
    assert route.call_count == 0
    assert result.pruned == 1


@pytest.mark.asyncio
@respx.mock
async def test_webpush_does_not_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_dns(monkeypatch, [FCM_IP])
    respx.post(FCM_PINNED).mock(
        return_value=httpx.Response(301, headers={"Location": "http://169.254.169.254/"})
    )
    metadata = respx.route(host="169.254.169.254").mock(return_value=httpx.Response(200))

    with pytest.raises(PushError, match="301"):
        await send_webpush(_vapid_settings(), FCM_ENDPOINT)
    assert metadata.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_webpush_transport_error_is_a_push_error_without_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_dns(monkeypatch, [FCM_IP])
    respx.post(FCM_PINNED).mock(side_effect=httpx.ConnectTimeout(f"timed out: {FCM_PINNED}"))

    with pytest.raises(PushError) as excinfo:
        await send_webpush(_vapid_settings(), FCM_ENDPOINT)
    assert str(excinfo.value) == "web push transport error: ConnectTimeout"


@pytest.mark.asyncio
async def test_webpush_unresolvable_host_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_answer(host: str) -> list[str]:
        raise OSError("Name or service not known")

    monkeypatch.setattr("app.services.live.push_endpoint._getaddrinfo", no_answer)
    with pytest.raises(PushError) as excinfo:
        await send_webpush(_vapid_settings(), FCM_ENDPOINT)
    # A plain PushError (retried), not PushGone (pruned).
    assert type(excinfo.value) is PushError


@pytest.mark.asyncio
async def test_webpush_without_keys_raises() -> None:
    with pytest.raises(PushError):
        await send_webpush(_settings(), FCM_ENDPOINT)


def test_vapid_jwt_is_valid_es256() -> None:
    priv = ec.generate_private_key(ec.SECP256R1())
    scalar = priv.private_numbers().private_value.to_bytes(32, "big")
    private_b64 = base64.urlsafe_b64encode(scalar).rstrip(b"=").decode()

    token = build_vapid_jwt(private_b64, subject="mailto:a@b.c", audience="https://push.example")
    header_b64, claims_b64, sig_b64 = token.split(".")

    claims = json.loads(_b64url_decode(claims_b64))
    assert claims["aud"] == "https://push.example"
    assert claims["sub"] == "mailto:a@b.c"
    assert "exp" in claims

    signing_input = f"{header_b64}.{claims_b64}".encode()
    raw = _b64url_decode(sig_b64)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    # Raises InvalidSignature if the signature does not verify.
    priv.public_key().verify(der, signing_input, ec.ECDSA(hashes.SHA256()))


@pytest.mark.asyncio
@respx.mock
async def test_push_task_commits_the_pruning(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ARQ task must commit: a 404/410 or a refused endpoint deleted in its
    session used to be rolled back, so the dead row was retried on every push."""
    from app.core.db import _write_sessionmaker
    from app.workers import tasks

    _fake_dns(monkeypatch, [FCM_IP])
    respx.post(FCM_PINNED).mock(return_value=httpx.Response(410))
    monkeypatch.setattr(tasks, "get_settings", _vapid_settings)
    async with _write_sessionmaker()() as s:
        user_id = await _make_user(s)
        fixture_id = await _make_fixture(s)
        s.add_all(
            [
                PushSubscription(
                    user_id=user_id, channel=PushChannel.webpush, endpoint=FCM_ENDPOINT
                ),
                PushSubscription(
                    user_id=user_id,
                    channel=PushChannel.webpush,
                    endpoint="http://mlflow:5000/api",
                ),
            ]
        )
        await _follow(s, user_id, fixture_id)
        await s.commit()

    await tasks.push_task({}, str(fixture_id), "hi")

    async with _write_sessionmaker()() as s:
        remaining = (
            await s.execute(
                select(func.count())
                .select_from(PushSubscription)
                .where(PushSubscription.user_id == user_id)
            )
        ).scalar_one()
    assert remaining == 0
