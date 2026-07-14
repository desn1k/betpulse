"""Live streaming (SSE) + push-subscription endpoints (Phase 5).

The SSE stream is tier-gated: unauthenticated (guest) requests get 401 and
``free`` users get 403 — only ``pro``/``expert`` may stream. On reconnect the
client's ``Last-Event-ID`` triggers a bounded replay from ``live_updates``,
after which the connection tails the Redis pub/sub channel so any replica can
deliver any update.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_read_session, get_session
from app.core.deps import (
    CurrentUser,
    get_redis_dep,
    get_settings_dep,
)
from app.models.live import PushSubscription
from app.models.user import User, UserTier
from app.schemas.live import PushSubscribeIn, PushSubscribeOut
from app.services.live.events import (
    format_sse,
    replay_since,
    subscribe_live_updates,
)

router = APIRouter(tags=["live"])


async def require_streaming_tier(user: CurrentUser) -> User:
    """Only paid tiers may open the live stream (guest already blocked by auth)."""
    if not UserTier(user.tier).can_stream_live:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Live streaming requires a Pro or Expert subscription",
        )
    return user


def _parse_last_event_id(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@router.get("/live/stream")
async def live_stream(
    request: Request,
    user: Annotated[User, Depends(require_streaming_tier)],
    session: Annotated[AsyncSession, Depends(get_read_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    resume_from = _parse_last_event_id(last_event_id)

    async def event_source() -> AsyncIterator[str]:
        if resume_from is not None:
            buffered = await replay_since(
                session,
                last_event_id=resume_from,
                window_seconds=settings.live_replay_window_seconds,
            )
            for update in buffered:
                yield format_sse(update.id, update.payload)
        async for event in subscribe_live_updates(redis, settings.live_events_channel):
            if await request.is_disconnected():
                break
            yield format_sse(int(event["id"]), event["payload"])

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/live/push/subscribe",
    response_model=PushSubscribeOut,
    status_code=status.HTTP_201_CREATED,
)
async def subscribe_push(
    body: PushSubscribeIn,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PushSubscribeOut:
    """Register (or refresh) a push destination for the current user."""
    stmt = (
        pg_insert(PushSubscription)
        .values(
            user_id=user.id,
            channel=body.channel,
            endpoint=body.endpoint,
            keys=body.keys,
        )
        .on_conflict_do_update(
            constraint="uq_push_subscription",
            set_={"keys": body.keys},
        )
        .returning(PushSubscription.id)
    )
    subscription_id = (await session.execute(stmt)).scalar_one()
    return PushSubscribeOut(id=subscription_id, channel=body.channel)
