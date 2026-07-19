"""Admin system-health checks (Phase 12d)."""

from __future__ import annotations

import time
from collections.abc import Awaitable
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.schemas.system import ComponentHealth, SystemHealthOut


async def _timed[T](coro: Awaitable[T]) -> tuple[T, int]:
    start = time.perf_counter()
    result = await coro
    return result, int((time.perf_counter() - start) * 1000)


async def check_database(session: AsyncSession) -> ComponentHealth:
    try:
        _, latency = await _timed(session.execute(text("select 1")))
        return ComponentHealth(name="postgres", status="ok", detail="reachable", latency_ms=latency)
    except Exception as exc:  # pragma: no cover - exercised by degraded API test via monkeypatch
        return ComponentHealth(name="postgres", status="error", detail=exc.__class__.__name__)


async def check_redis(redis: Redis) -> ComponentHealth:
    try:
        _, latency = await _timed(redis.ping())
        return ComponentHealth(name="redis", status="ok", detail="reachable", latency_ms=latency)
    except Exception as exc:  # pragma: no cover - exercised by degraded API test via monkeypatch
        return ComponentHealth(name="redis", status="error", detail=exc.__class__.__name__)


def check_ops_alerts(settings: Settings) -> ComponentHealth:
    configured = bool(settings.telegram_bot_token and settings.telegram_alert_chat_id)
    return ComponentHealth(
        name="ops_alerts",
        status="ok" if configured else "not_configured",
        detail=(
            "telegram configured" if configured else "missing Telegram bot token or alert chat id"
        ),
        meta={"channel": "telegram"},
    )


async def build_system_health(
    session: AsyncSession, redis: Redis, settings: Settings
) -> SystemHealthOut:
    components = [
        await check_database(session),
        await check_redis(redis),
        check_ops_alerts(settings),
    ]
    hard_failures = [c for c in components if c.status == "error"]
    soft_failures = [c for c in components if c.status in ("degraded", "not_configured")]
    status = "error" if hard_failures else ("degraded" if soft_failures else "ok")
    return SystemHealthOut(status=status, checked_at=datetime.now(UTC), components=components)
