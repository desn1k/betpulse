"""ARQ task bodies and their testable orchestrators."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import _write_sessionmaker
from app.core.redis import get_redis
from app.ml.evaluation import compute_rolling_metrics
from app.ml.registry import apply_champion_selection
from app.ml.training import TrainingSummary, run_training
from app.providers.football_data_couk import FootballDataCoUkProvider
from app.services.ingestion.runner import network_csv_source, run_recorded_ingestion
from app.services.live.events import publish_live_update
from app.services.live.ingestion import poll_live
from app.services.live.provider import build_live_provider
from app.services.live.push import dispatch_push
from app.services.live.recompute import get_base_rates, recompute_fixture
from app.services.llm.ranking import rank_today_fixtures
from app.services.model_admin import get_weighting

logger = logging.getLogger("workers.tasks")

CHAMPION_LOCK_KEY = "lock:champion_reeval"
LIVE_POLL_LOCK_KEY = "lock:live_poll"


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


def _swing_text(fixture_id: uuid.UUID, probs: dict[str, float]) -> str:
    return (
        f"Live probabilities moved for fixture {fixture_id}: "
        f"home {probs['home']:.0%} / draw {probs['draw']:.0%} / away {probs['away']:.0%}"
    )


async def poll_live_task(ctx: dict[str, Any]) -> int:
    """Poll the live provider, upsert in-play state, enqueue a recompute per
    changed fixture, then re-schedule itself. A Redis single-flight lock stops a
    slow poll from overlapping the next tick."""
    settings = get_settings()
    redis = get_redis()
    token = secrets.token_hex(16)
    ttl = 2 * settings.live_poll_interval_seconds
    acquired = await redis.set(LIVE_POLL_LOCK_KEY, token, nx=True, ex=ttl)
    if not acquired:
        logger.info("live poll skipped: lock held by another worker")
        return 0

    arq = ctx.get("redis")
    try:
        provider = build_live_provider(settings)
        async with _write_sessionmaker()() as session:
            result = await poll_live(session, provider)
            await session.commit()
        if arq is not None:
            for state in result.states:
                await arq.enqueue_job(
                    "recompute_fixture_task",
                    str(state.fixture_id),
                    state.minute,
                    state.home_score,
                    state.away_score,
                )
        return result.ingested
    finally:
        if await redis.get(LIVE_POLL_LOCK_KEY) == token:
            await redis.delete(LIVE_POLL_LOCK_KEY)
        if arq is not None:
            await arq.enqueue_job(
                "poll_live_task",
                _defer_by=timedelta(seconds=settings.live_poll_interval_seconds),
            )


async def recompute_fixture_task(
    ctx: dict[str, Any],
    fixture_id: str,
    minute: int,
    home_score: int,
    away_score: int,
) -> float:
    """Recompute one fixture's in-play probabilities, fan them out over Redis
    pub/sub, and enqueue a push job on a significant swing."""
    settings = get_settings()
    redis = get_redis()
    fid = uuid.UUID(fixture_id)
    async with _write_sessionmaker()() as session:
        base_rates = await get_base_rates(session, fid)
        result = await recompute_fixture(
            session,
            fixture_id=fid,
            minute=minute,
            home_score=home_score,
            away_score=away_score,
            base_rates=base_rates,
            swing_threshold=settings.probability_swing_push_threshold,
        )
        await session.commit()

    if result.changed and result.live_update_id is not None:
        await publish_live_update(
            redis,
            settings.live_events_channel,
            result.live_update_id,
            {
                "fixture_id": str(fid),
                "minute": result.minute,
                "home_score": result.home_score,
                "away_score": result.away_score,
                "probs": result.probs,
            },
        )
    if result.should_push:
        arq = ctx.get("redis")
        if arq is not None:
            await arq.enqueue_job("push_task", str(fid), _swing_text(fid, result.probs))
    return result.swing


async def push_task(ctx: dict[str, Any], fixture_id: str, text: str) -> int:
    """Deliver a swing push to every subscription (rate-limited per fixture)."""
    settings = get_settings()
    redis = get_redis()
    async with _write_sessionmaker()() as session:
        result = await dispatch_push(
            session, redis, fixture_id=uuid.UUID(fixture_id), text=text, settings=settings
        )
    return result.delivered


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
            # The weighting mode is admin-editable at runtime (Phase 12b); in
            # manual mode the re-eval leaves the admin-set weights untouched.
            mode = (await get_weighting(session)).mode
            champion = await reevaluate_champions(
                session,
                window_days=settings.accuracy_window_days,
                min_samples=settings.champion_min_samples,
                weight_mode=mode.value,
            )
            await session.commit()
        return champion
    finally:
        if await redis.get(CHAMPION_LOCK_KEY) == token:
            await redis.delete(CHAMPION_LOCK_KEY)


LLM_RANK_LOCK_KEY = "lock:llm_rank"


async def rank_llm_fixtures_task(ctx: dict[str, Any]) -> int:
    """Midnight LLM-analysis ranking of today's scheduled fixtures. A Redis lock
    with a TTL guarantees a single runner."""
    redis = get_redis()
    token = secrets.token_hex(16)
    acquired = await redis.set(LLM_RANK_LOCK_KEY, token, nx=True, ex=600)
    if not acquired:
        logger.info("llm ranking skipped: lock held by another worker")
        return 0
    try:
        async with _write_sessionmaker()() as session:
            ranked = await rank_today_fixtures(session)
            await session.commit()
        return ranked
    finally:
        if await redis.get(LLM_RANK_LOCK_KEY) == token:
            await redis.delete(LLM_RANK_LOCK_KEY)


async def ingest_history_task(
    ctx: dict[str, Any],
    leagues: list[str],
    seasons: list[str],
    triggered_by: str | None = None,
) -> int:
    """Admin-triggered historical re-scan (football-data). Records one
    ``ingestion_runs`` row per (league, season) pair. Returns the run count."""
    provider = FootballDataCoUkProvider()
    source = network_csv_source(provider)
    async with _write_sessionmaker()() as session:
        runs = await run_recorded_ingestion(
            session,
            leagues=leagues,
            seasons=seasons,
            csv_source=source,
            provider_name=provider.name,
            triggered_by=triggered_by,
        )
        await session.commit()
    return len(runs)
