"""SSE event plumbing: Redis pub/sub fan-out and reconnect replay.

Live updates are fanned out over a Redis pub/sub channel so any API replica can
push an event to any connected SSE client (horizontal scale, spec §18). On
reconnect a client sends ``Last-Event-ID``; we replay the buffered
``live_updates`` rows newer than that id, bounded to a short window so a client
that was away for hours does not get an unbounded backlog.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live import LiveUpdate


async def publish_live_update(
    redis: Redis, channel: str, event_id: int, payload: dict[str, Any]
) -> None:
    """Fan out one live update to all subscribers (all replicas)."""
    await redis.publish(channel, json.dumps({"id": event_id, "payload": payload}))


async def subscribe_live_updates(
    redis: Redis, channel: str
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield ``{"id", "payload"}`` dicts as they are published on ``channel``."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode()
            yield json.loads(data)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[no-untyped-call]


async def replay_since(
    session: AsyncSession,
    *,
    last_event_id: int,
    window_seconds: int,
    now: datetime | None = None,
    limit: int = 500,
) -> list[LiveUpdate]:
    """Buffered updates with id greater than ``last_event_id`` within the window."""
    now = now or datetime.now(tz=UTC)
    cutoff = now - timedelta(seconds=window_seconds)
    stmt = (
        select(LiveUpdate)
        .where(LiveUpdate.id > last_event_id, LiveUpdate.created_at >= cutoff)
        .order_by(LiveUpdate.id)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


def format_sse(event_id: int, payload: dict[str, Any]) -> str:
    """Render one Server-Sent Event frame (``id:`` + ``data:`` + blank line)."""
    return f"id: {event_id}\ndata: {json.dumps(payload)}\n\n"
