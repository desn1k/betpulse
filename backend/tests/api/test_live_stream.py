"""Live SSE endpoint: tier gating and push-subscription registration."""

from __future__ import annotations

import uuid

import pytest
from app.api.live import require_streaming_tier
from app.core.security import create_access_token
from app.models.live import PushChannel, PushSubscription
from app.models.user import User, UserTier
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _user(tier: UserTier) -> User:
    return User(email="x@y.com", password_hash="x", tier=tier)


@pytest.mark.asyncio
async def test_streaming_tier_dependency_blocks_free_allows_pro(session: AsyncSession) -> None:
    from app.core.redis import get_redis

    redis = get_redis()
    # No subscriptions seeded → tier resolves from users.tier, flags from the
    # code defaults (live_recompute: free=False, pro=True).
    with pytest.raises(HTTPException) as exc:
        await require_streaming_tier(_user(UserTier.free), session, redis)
    assert exc.value.status_code == 403

    allowed = await require_streaming_tier(_user(UserTier.pro), session, redis)
    assert allowed.tier == UserTier.pro


async def _authed_headers(session: AsyncSession, tier: UserTier) -> tuple[dict[str, str], User]:
    user = User(email=f"{uuid.uuid4()}@x.com", password_hash="x", tier=tier)
    session.add(user)
    await session.commit()
    token = create_access_token(subject=str(user.id), role=user.role.value)
    return {"Authorization": f"Bearer {token}"}, user


@pytest.mark.asyncio
async def test_stream_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/live/stream")).status_code == 401


@pytest.mark.asyncio
async def test_stream_forbidden_for_free_tier(client: AsyncClient, session: AsyncSession) -> None:
    headers, _ = await _authed_headers(session, UserTier.free)
    resp = await client.get("/live/stream", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_push_subscribe_persists_row(client: AsyncClient, session: AsyncSession) -> None:
    headers, user = await _authed_headers(session, UserTier.pro)
    resp = await client.post(
        "/live/push/subscribe",
        headers=headers,
        json={"channel": "telegram", "endpoint": "12345"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["channel"] == "telegram"

    sub = (
        await session.execute(select(PushSubscription).where(PushSubscription.user_id == user.id))
    ).scalar_one()
    assert sub.channel == PushChannel.telegram
    assert sub.endpoint == "12345"
