"""Push dispatch: rate limiting, one-retry-then-discard, and VAPID signing."""

from __future__ import annotations

import base64
import json
import uuid

import httpx
import pytest
import respx
from app.core.config import Settings, get_settings
from app.core.redis import get_redis
from app.models.live import PushChannel, PushSubscription
from app.models.user import User
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
from sqlalchemy.ext.asyncio import AsyncSession

TELEGRAM_URL = "https://api.telegram.org/botTEST/sendMessage"


def _settings(**overrides: object) -> Settings:
    base = {
        "telegram_bot_token": "TEST",
        "telegram_api_base_url": "https://api.telegram.org",
        "push_rate_limit_seconds": 300,
        "push_retry_delay_seconds": 0,
    }
    base.update(overrides)
    return get_settings().model_copy(update=base)


async def _make_user(session: AsyncSession) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user.id


async def _noop_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_allows_one_push_per_window(session: AsyncSession) -> None:
    route = respx.post(TELEGRAM_URL).mock(return_value=httpx.Response(200))
    user_id = await _make_user(session)
    session.add(PushSubscription(user_id=user_id, channel=PushChannel.telegram, endpoint="12345"))
    await session.flush()

    fixture_id = uuid.uuid4()
    settings = _settings()
    redis = get_redis()

    first = await dispatch_push(session, redis, fixture_id=fixture_id, text="hi", settings=settings)
    second = await dispatch_push(
        session, redis, fixture_id=fixture_id, text="hi", settings=settings
    )

    assert first.delivered == 1 and first.rate_limited is False
    assert second.rate_limited is True and second.delivered == 0
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_skips_when_no_subscription(session: AsyncSession) -> None:
    result = await dispatch_push(
        session, get_redis(), fixture_id=uuid.uuid4(), text="hi", settings=_settings()
    )
    assert result.skipped_no_subscription is True
    assert result.delivered == 0


@pytest.mark.asyncio
@respx.mock
async def test_failure_retries_once_then_discards(session: AsyncSession) -> None:
    route = respx.post(TELEGRAM_URL).mock(return_value=httpx.Response(500))
    user_id = await _make_user(session)
    session.add(PushSubscription(user_id=user_id, channel=PushChannel.telegram, endpoint="12345"))
    await session.flush()

    result = await dispatch_push(
        session,
        get_redis(),
        fixture_id=uuid.uuid4(),
        text="hi",
        settings=_settings(),
        sleep=_noop_sleep,
    )
    assert result.failed == 1
    assert result.delivered == 0
    assert route.call_count == 2  # original + exactly one retry


@pytest.mark.asyncio
@respx.mock
async def test_webpush_sends_vapid_authenticated_request() -> None:
    endpoint = "https://push.example.com/sub/abc"
    route = respx.post(endpoint).mock(return_value=httpx.Response(201))
    priv = ec.generate_private_key(ec.SECP256R1())
    scalar = priv.private_numbers().private_value.to_bytes(32, "big")
    settings = _settings(
        webpush_vapid_private_key=base64.urlsafe_b64encode(scalar).rstrip(b"=").decode(),
        webpush_vapid_public_key="BPUBLICKEY",
        webpush_contact_email="admin@example.com",
    )

    await send_webpush(settings, endpoint)

    assert route.called
    auth = route.calls.last.request.headers["Authorization"]
    assert auth.startswith("vapid t=")
    assert "k=BPUBLICKEY" in auth


@pytest.mark.asyncio
async def test_webpush_without_keys_raises() -> None:
    with pytest.raises(PushError):
        await send_webpush(_settings(), "https://push.example.com/sub/abc")


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
