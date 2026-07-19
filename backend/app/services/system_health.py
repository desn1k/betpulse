"""Admin system-health checks (Phase 12d)."""

from __future__ import annotations

import time
from collections.abc import Awaitable
from datetime import UTC, datetime
from decimal import Decimal

from arq.constants import default_queue_name, health_check_key_suffix
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.ingestion_run import IngestionRun
from app.models.llm import LlmAnalysis
from app.models.model_registry import ModelRegistry
from app.schemas.system import ComponentHealth, SystemHealthOut


async def _timed[T](coro: Awaitable[T]) -> tuple[T, int]:
    start = time.perf_counter()
    result = await coro
    return result, int((time.perf_counter() - start) * 1000)


def check_api() -> ComponentHealth:
    return ComponentHealth(name="api", status="ok", detail="process reachable")


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


async def check_arq(redis: Redis) -> ComponentHealth:
    queue_key = default_queue_name
    health_key = f"{queue_key}{health_check_key_suffix}"
    try:
        start = time.perf_counter()
        queue_depth = int(await redis.zcard(queue_key))
        worker_seen = bool(await redis.exists(health_key))
        latency = int((time.perf_counter() - start) * 1000)
        return ComponentHealth(
            name="arq",
            status="ok" if worker_seen else "degraded",
            detail="worker heartbeat present" if worker_seen else "worker heartbeat not observed",
            latency_ms=latency,
            meta={"queue": queue_key, "queue_depth": queue_depth, "worker_seen": worker_seen},
        )
    except Exception as exc:  # pragma: no cover - exercised by degraded API test via monkeypatch
        return ComponentHealth(name="arq", status="error", detail=exc.__class__.__name__)


async def check_latest_ingestion(session: AsyncSession) -> ComponentHealth:
    row = (
        await session.execute(
            select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return ComponentHealth(
            name="latest_ingestion", status="not_configured", detail="no runs yet"
        )
    status = (
        "ok"
        if row.status.value == "success"
        else ("degraded" if row.status.value in {"running", "partial"} else "error")
    )
    return ComponentHealth(
        name="latest_ingestion",
        status=status,
        detail=f"{row.provider}:{row.league or '-'}:{row.season or '-'} {row.status.value}",
        meta={
            "status": row.status.value,
            "fixtures_ingested": row.fixtures_ingested,
            "odds_ingested": row.odds_ingested,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        },
    )


async def check_latest_model_evaluation(session: AsyncSession) -> ComponentHealth:
    evaluated_at = (
        await session.execute(select(func.max(ModelRegistry.last_evaluated_at)))
    ).scalar_one_or_none()
    if evaluated_at is None:
        return ComponentHealth(
            name="latest_model_evaluation",
            status="not_configured",
            detail="no evaluated models yet",
        )
    return ComponentHealth(
        name="latest_model_evaluation",
        status="ok",
        detail=evaluated_at.isoformat(),
        meta={"last_evaluated_at": evaluated_at.isoformat()},
    )


async def check_llm_spend_today(session: AsyncSession) -> ComponentHealth:
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    tokens_in, tokens_out, cost = (
        await session.execute(
            select(
                func.coalesce(func.sum(LlmAnalysis.tokens_in), 0),
                func.coalesce(func.sum(LlmAnalysis.tokens_out), 0),
                func.coalesce(func.sum(LlmAnalysis.cost), 0),
            ).where(LlmAnalysis.created_at >= today_start)
        )
    ).one()
    cost_value = (cost if isinstance(cost, Decimal) else Decimal(str(cost))).quantize(
        Decimal("0.000001")
    )
    return ComponentHealth(
        name="llm_spend_today",
        status="ok",
        detail="UTC day token spend",
        meta={
            "tokens_in": int(tokens_in),
            "tokens_out": int(tokens_out),
            "tokens_total": int(tokens_in) + int(tokens_out),
            "cost": str(cost_value),
        },
    )


def check_backup_status() -> ComponentHealth:
    return ComponentHealth(
        name="backup",
        status="not_configured",
        detail="backup checks land in Phase 14",
    )


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
        check_api(),
        await check_database(session),
        await check_redis(redis),
        await check_arq(redis),
        await check_latest_ingestion(session),
        await check_latest_model_evaluation(session),
        await check_llm_spend_today(session),
        check_backup_status(),
        check_ops_alerts(settings),
    ]
    hard_failures = [c for c in components if c.status == "error"]
    soft_failures = [c for c in components if c.status in ("degraded", "not_configured")]
    status = "error" if hard_failures else ("degraded" if soft_failures else "ok")
    return SystemHealthOut(status=status, checked_at=datetime.now(UTC), components=components)
