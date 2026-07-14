"""ARQ task bodies and their testable orchestrators."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import _write_sessionmaker
from app.core.redis import get_redis
from app.ml.evaluation import compute_rolling_metrics
from app.ml.registry import apply_champion_selection
from app.ml.training import TrainingSummary, run_training

logger = logging.getLogger("workers.tasks")

CHAMPION_LOCK_KEY = "lock:champion_reeval"


async def reevaluate_champions(
    session: AsyncSession,
    *,
    window_days: int,
    min_samples: int,
    weight_mode: str,
    now: datetime | None = None,
) -> str | None:
    """Recompute rolling OOS metrics and re-select the champion. Idempotent."""
    metrics = await compute_rolling_metrics(session, window_days=window_days, now=now)
    return await apply_champion_selection(
        session, metrics, weight_mode=weight_mode, min_samples=min_samples
    )


# --- ARQ entrypoints --------------------------------------------------------


async def train_all_task(ctx: dict[str, Any]) -> TrainingSummary:
    async with _write_sessionmaker()() as session:
        summary = await run_training(session)
        await session.commit()
    return summary


async def reevaluate_champions_task(ctx: dict[str, Any]) -> str | None:
    """Nightly champion re-evaluation. A Redis lock with a TTL guarantees a
    single runner and prevents a crashed worker from wedging the job forever.
    TTL = 2 * expected max runtime."""
    settings = get_settings()
    redis = get_redis()
    ttl = 2 * settings.champion_reeval_max_runtime_seconds
    token = secrets.token_hex(16)
    acquired = await redis.set(CHAMPION_LOCK_KEY, token, nx=True, ex=ttl)
    if not acquired:
        logger.info("champion reeval skipped: lock held by another worker")
        return None
    try:
        async with _write_sessionmaker()() as session:
            champion = await reevaluate_champions(
                session,
                window_days=settings.accuracy_window_days,
                min_samples=settings.champion_min_samples,
                weight_mode=settings.consensus_weight_mode,
            )
            await session.commit()
        return champion
    finally:
        if await redis.get(CHAMPION_LOCK_KEY) == token:
            await redis.delete(CHAMPION_LOCK_KEY)
