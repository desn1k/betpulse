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
from app.services.client_identity import check_client_identity


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


# Production runs `noeviction` with a `maxmemory` (infra/docker-compose.yml): every
# quota, rate-limit and ARQ key carries a TTL, so no eviction policy spares them,
# and a full Redis refuses writes instead. Degraded from here on, before that.
REDIS_MEMORY_DEGRADED_RATIO = 0.8
_MB = 1024 * 1024


async def check_redis_memory(redis: Redis) -> ComponentHealth:
    name = "redis_memory"
    try:
        info = await redis.info("memory")
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return ComponentHealth(name=name, status="degraded", detail=exc.__class__.__name__)
    used = int(info.get("used_memory", 0))
    maxmemory = int(info.get("maxmemory", 0))
    policy = str(info.get("maxmemory_policy", ""))
    meta: dict[str, object] = {
        "used_bytes": used,
        "maxmemory_bytes": maxmemory,
        "used_percent": round(100 * used / maxmemory, 1) if maxmemory else None,
        "policy": policy,
    }
    if not maxmemory:
        return ComponentHealth(
            name=name,
            status="not_configured",
            detail=f"maxmemory is not set ({used / _MB:.1f} MB used): Redis grows until "
            "its container is killed",
            meta=meta,
        )
    usage = f"{meta['used_percent']}% of maxmemory ({used / _MB:.1f} of {maxmemory / _MB:.0f} MB)"
    if policy != "noeviction":
        return ComponentHealth(
            name=name,
            status="degraded",
            detail=f"policy {policy} may evict quota, rate-limit and ARQ keys; expected "
            f"noeviction. {usage}",
            meta=meta,
        )
    if used > REDIS_MEMORY_DEGRADED_RATIO * maxmemory:
        return ComponentHealth(
            name=name,
            status="degraded",
            detail=f"{usage}: at 100% Redis refuses writes (quotas, rate limits, job queues)",
            meta=meta,
        )
    return ComponentHealth(name=name, status="ok", detail=usage, meta=meta)


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
        await check_redis_memory(redis),
        check_ops_alerts(settings),
        await check_client_identity(redis, settings),
    ]
    hard_failures = [c for c in components if c.status == "error"]
    soft_failures = [c for c in components if c.status in ("degraded", "not_configured")]
    status = "error" if hard_failures else ("degraded" if soft_failures else "ok")
    return SystemHealthOut(status=status, checked_at=datetime.now(UTC), components=components)
