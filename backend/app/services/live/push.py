"""Push notifications on a probability swing (queue: push).

Two channels: Telegram (Bot HTTP API) and Web Push (VAPID). Delivery is
rate-limited per subscriber to at most one push per (user, fixture) per window
in Redis, skipped entirely when a user has no subscription, and retried exactly
once on failure before being logged and discarded. The Web Push payload is a VAPID-authenticated
data-less tickle; encrypting a payload body (RFC 8291) can be added later without
changing this trigger path.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.live import PushChannel, PushSubscription

logger = logging.getLogger("live.push")

SleepFn = Callable[[float], Awaitable[None]]


class PushError(Exception):
    """Raised when a single push delivery fails."""


@dataclass(slots=True)
class PushDispatchResult:
    # Number of subscriptions suppressed by the per-(user, fixture) rate limit.
    rate_limited: int = 0
    delivered: int = 0
    failed: int = 0
    skipped_no_subscription: bool = False


# --- VAPID (Web Push auth) --------------------------------------------------


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def build_vapid_jwt(
    private_key_b64: str, *, subject: str, audience: str, ttl_seconds: int = 12 * 3600
) -> str:
    """Build a signed ES256 VAPID JWT for the given push-service audience.

    ``private_key_b64`` is the base64url-encoded 32-byte P-256 private scalar.
    """
    scalar = int.from_bytes(_b64url_decode(private_key_b64), "big")
    key = ec.derive_private_key(scalar, ec.SECP256R1())

    header = _b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = _b64url_encode(
        json.dumps(
            {"aud": audience, "exp": int(time.time()) + ttl_seconds, "sub": subject}
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode()
    der_sig = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{header}.{claims}.{_b64url_encode(raw_sig)}"


def _audience(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


# --- Single-channel senders -------------------------------------------------


async def send_telegram(settings: Settings, chat_id: str, text: str) -> None:
    url = f"{settings.telegram_api_base_url}/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json={"chat_id": chat_id, "text": text})
    if resp.status_code >= 400:
        raise PushError(f"telegram send failed: {resp.status_code}")


async def send_webpush(settings: Settings, endpoint: str) -> None:
    if not settings.webpush_vapid_private_key or not settings.webpush_vapid_public_key:
        raise PushError("web push not configured (missing VAPID keys)")
    jwt = build_vapid_jwt(
        settings.webpush_vapid_private_key,
        subject=settings.vapid_subject,
        audience=_audience(endpoint),
    )
    headers = {
        "Authorization": f"vapid t={jwt}, k={settings.webpush_vapid_public_key}",
        "TTL": "300",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(endpoint, headers=headers, content=b"")
    if resp.status_code >= 400:
        raise PushError(f"web push failed: {resp.status_code}")


async def _deliver_one(settings: Settings, sub: PushSubscription, text: str) -> None:
    if sub.channel == PushChannel.telegram:
        await send_telegram(settings, sub.endpoint, text)
    else:
        await send_webpush(settings, sub.endpoint)


# --- Orchestration ----------------------------------------------------------


async def dispatch_push(
    session: AsyncSession,
    redis: Redis,
    *,
    fixture_id: uuid.UUID,
    text: str,
    settings: Settings,
    sleep: SleepFn = asyncio.sleep,
) -> PushDispatchResult:
    """Deliver a swing notification to each subscription, once per (user, fixture)/window."""
    subs = list((await session.execute(select(PushSubscription))).scalars())
    if not subs:
        return PushDispatchResult(skipped_no_subscription=True)

    result = PushDispatchResult()
    for sub in subs:
        # Rate-limit per subscriber: a given user gets at most one push per
        # fixture per window, checked before we attempt delivery to them.
        rl_key = f"push:rl:{sub.user_id}:{fixture_id}"
        acquired = await redis.set(rl_key, "1", nx=True, ex=settings.push_rate_limit_seconds)
        if not acquired:
            result.rate_limited += 1
            logger.info(
                "push suppressed by rate limit for user %s fixture %s", sub.user_id, fixture_id
            )
            continue
        try:
            await _deliver_one(settings, sub, text)
            result.delivered += 1
        except PushError:
            await sleep(settings.push_retry_delay_seconds)
            try:
                await _deliver_one(settings, sub, text)
                result.delivered += 1
            except PushError as exc:
                result.failed += 1
                logger.warning(
                    json.dumps(
                        {
                            "event": "push_delivery_failed",
                            "fixture_id": str(fixture_id),
                            "channel": sub.channel.value,
                            "error": str(exc),
                        }
                    )
                )
    return result
