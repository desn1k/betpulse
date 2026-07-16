"""Live streaming (SSE) + push-subscription endpoints (Phase 5).

The SSE stream is tier-gated: unauthenticated (guest) requests get 401 and
``free`` users get 403 — only ``pro``/``expert`` may stream. On reconnect the
client's ``Last-Event-ID`` triggers a bounded replay from ``live_updates``,
after which the connection tails the Redis pub/sub channel so any replica can
deliver any update.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.db import get_read_session, get_session
from app.core.deps import (
    CurrentUser,
    get_db,
    get_redis_dep,
    get_settings_dep,
    require_push_tier,
)
from app.models.fixture import Fixture
from app.models.live import LiveUpdate, PushSubscription
from app.models.reference import Team
from app.models.user import User
from app.schemas.live import PushSubscribeIn, PushSubscribeOut
from app.schemas.push import FollowOut, FollowsOut, LatestSwingOut
from app.services.live.events import (
    format_sse,
    replay_since,
    subscribe_live_updates,
)
from app.services.push.follows import (
    follow_fixture,
    followed_fixture_ids,
    unfollow_fixture,
)
from app.services.tiers import resolve_tier_context

router = APIRouter(tags=["live"])


async def require_streaming_tier(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> User:
    """Only tiers with the ``live_recompute`` feature flag may open the live
    stream (guest is already blocked by auth). Same single source of truth as the
    rest of the tier gating — flags in ``tiers``, resolved via subscriptions."""
    tier = await resolve_tier_context(session, redis, user)
    if not bool(tier.feature_flags.get("live_recompute", False)):
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
    user: Annotated[User, Depends(require_push_tier)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PushSubscribeOut:
    """Register (or refresh) a push destination for the current user (Pro/Expert)."""
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


@router.get("/live/push/follows", response_model=FollowsOut)
async def list_follows(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FollowsOut:
    """Fixtures the caller follows (drives the "notify me" toggle state)."""
    return FollowsOut(fixture_ids=await followed_fixture_ids(session, user_id=user.id))


@router.put("/live/push/follow/{fixture_id}", response_model=FollowOut)
async def follow_match(
    fixture_id: uuid.UUID,
    user: Annotated[User, Depends(require_push_tier)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FollowOut:
    """Follow a fixture to receive its swing pushes (Pro/Expert). Idempotent."""
    if await session.get(Fixture, fixture_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    await follow_fixture(session, user_id=user.id, fixture_id=fixture_id)
    await session.commit()
    return FollowOut(fixture_id=fixture_id, following=True)


@router.delete("/live/push/follow/{fixture_id}", response_model=FollowOut)
async def unfollow_match(
    fixture_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> FollowOut:
    """Stop following a fixture. Allowed for any authenticated user (so a
    downgraded user can still clean up)."""
    await unfollow_fixture(session, user_id=user.id, fixture_id=fixture_id)
    await session.commit()
    return FollowOut(fixture_id=fixture_id, following=False)


@router.get("/live/push/latest/{fixture_id}", response_model=LatestSwingOut)
async def latest_swing(
    fixture_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_read_session)],
) -> LatestSwingOut:
    """Public latest live snapshot for a fixture — the service worker fetches this
    on a push tickle to render the notification (same data as the live card)."""
    home_team = aliased(Team)
    away_team = aliased(Team)
    row = (
        await session.execute(
            select(LiveUpdate, home_team.name, away_team.name)
            .join(Fixture, Fixture.id == LiveUpdate.fixture_id)
            .join(home_team, home_team.id == Fixture.home_team_id)
            .join(away_team, away_team.id == Fixture.away_team_id)
            .where(LiveUpdate.fixture_id == fixture_id)
            .order_by(LiveUpdate.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No live update")
    update, home_name, away_name = row
    probs = update.payload.get("probs", {}) if isinstance(update.payload, dict) else {}
    return LatestSwingOut(
        fixture_id=fixture_id,
        home_team=home_name,
        away_team=away_name,
        minute=update.minute,
        home_score=update.home_score,
        away_score=update.away_score,
        probs=probs,
    )
