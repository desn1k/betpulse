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
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.live import PushChannel, PushFollow, PushSubscription
from app.models.user import User
from app.services.limits import push_budget_remaining, record_push_delivered
from app.services.tiers import resolve_tier_context

logger = logging.getLogger("live.push")

SleepFn = Callable[[float], Awaitable[None]]


class PushError(Exception):
    """Raised when a single push delivery fails."""


class PushGone(PushError):
    """The push endpoint is permanently gone (HTTP 404/410) — prune it."""


@dataclass(slots=True)
class PushDispatchResult:
    # Number of users suppressed by the per-(user, fixture) rate-limit window.
    rate_limited: int = 0
    delivered: int = 0
    failed: int = 0
    # Users skipped because their daily push budget (pushes_per_day) is spent.
    budget_exhausted: int = 0
    # Dead Web Push subscriptions (404/410) deleted from the DB.
    pruned: int = 0
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
    if resp.status_code in (404, 410):
        # The browser dropped this subscription; it will never work again.
        raise PushGone(f"web push gone: {resp.status_code}")
    if resp.status_code >= 400:
        raise PushError(f"web push failed: {resp.status_code}")


async def _deliver_one(settings: Settings, sub: PushSubscription, text: str) -> None:
    if sub.channel == PushChannel.telegram:
        await send_telegram(settings, sub.endpoint, text)
    else:
        await send_webpush(settings, sub.endpoint)


# --- Orchestration ----------------------------------------------------------


async def _deliver_with_retry(
    session: AsyncSession,
    sub: PushSubscription,
    text: str,
    settings: Settings,
    sleep: SleepFn,
    result: PushDispatchResult,
    fixture_id: uuid.UUID,
) -> bool:
    """Deliver to one subscription, retrying once. Prunes a gone (404/410) Web
    Push endpoint. Returns True iff the notification was delivered."""
    for attempt in (1, 2):
        try:
            await _deliver_one(settings, sub, text)
            return True
        except PushGone:
            await session.delete(sub)
            result.pruned += 1
            return False
        except PushError as exc:
            if attempt == 1:
                await sleep(settings.push_retry_delay_seconds)
                continue
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
    return False


async def dispatch_push(
    session: AsyncSession,
    redis: Redis,
    *,
    fixture_id: uuid.UUID,
    text: str,
    settings: Settings,
    sleep: SleepFn = asyncio.sleep,
    now: datetime | None = None,
) -> PushDispatchResult:
    """Deliver a swing notification to the fixture's **followers** only.

    Per user: at most one push per (user, fixture)/window, hard-stopped by the
    per-UTC-day ``pushes_per_day`` budget (checked before delivery, counted only
    on success). Dead Web Push endpoints are pruned.
    """
    now = now or datetime.now(UTC)
    # Only users who follow this fixture, and only their reachable subscriptions.
    subs = list(
        (
            await session.execute(
                select(PushSubscription)
                .join(PushFollow, PushFollow.user_id == PushSubscription.user_id)
                .where(PushFollow.fixture_id == fixture_id)
            )
        ).scalars()
    )
    if not subs:
        return PushDispatchResult(skipped_no_subscription=True)

    by_user: dict[uuid.UUID, list[PushSubscription]] = defaultdict(list)
    for sub in subs:
        by_user[sub.user_id].append(sub)

    result = PushDispatchResult()
    for user_id, user_subs in by_user.items():
        # Rate-limit window: a user gets at most one push per fixture per window.
        rl_key = f"push:rl:{user_id}:{fixture_id}"
        if not await redis.set(rl_key, "1", nx=True, ex=settings.push_rate_limit_seconds):
            result.rate_limited += 1
            continue

        # Daily budget hard-stop, checked before we attempt any delivery.
        limit = await _pushes_per_day(session, redis, user_id)
        remaining = await push_budget_remaining(redis, user_id=user_id, limit=limit, now=now)
        if remaining is not None and remaining <= 0:
            result.budget_exhausted += 1
            await redis.delete(rl_key)  # did not deliver — free the window
            continue

        delivered = False
        for sub in user_subs:
            if await _deliver_with_retry(session, sub, text, settings, sleep, result, fixture_id):
                delivered = True
                break  # one channel per user per swing is enough
        if delivered:
            result.delivered += 1
            await record_push_delivered(redis, user_id=user_id, now=now)
        else:
            await redis.delete(rl_key)  # nothing delivered — let a retry try again
    return result


async def _pushes_per_day(session: AsyncSession, redis: Redis, user_id: uuid.UUID) -> int:
    """Resolve a user's ``pushes_per_day`` limit (-1 unlimited / 0 none)."""
    user = await session.get(User, user_id)
    if user is None:
        return 0
    tier = await resolve_tier_context(session, redis, user)
    return tier.pushes_per_day()
