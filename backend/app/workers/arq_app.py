"""ARQ worker settings — one class per queue (spec §18).

Run each as its own process / Compose service::

    arq app.workers.arq_app.RealtimeWorker   # live poll, in-play recompute, push
    arq app.workers.arq_app.BatchWorker      # ingestion re-scans, LLM ranking cron
    arq app.workers.arq_app.MlWorker         # training, champion re-evaluation cron

Queue names and the task → queue mapping live in :mod:`app.workers.queues`.
Each cron is registered on exactly one worker class; the Redis locks inside the
cron tasks still make extra replicas of that worker safe hot standbys.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Protocol

from arq.connections import RedisSettings
from arq.cron import CronJob, cron
from arq.typing import WorkerCoroutine
from arq.worker import Function, func

from app.core.config import get_settings
from app.core.outbound import install_log_safety
from app.services.live.provider import live_provider_configured
from app.workers.queues import Queue, enqueue, queue_for
from app.workers.tasks import (
    ingest_history_task,
    poll_live_task,
    push_task,
    rank_llm_fixtures_task,
    recompute_fixture_task,
    reevaluate_champions_task,
    train_all_task,
)

# Heartbeat refresh; `python -m app.workers.healthcheck <queue>` (the Compose
# healthcheck) fails once ARQ lets the key expire shortly after this.
HEALTH_CHECK_INTERVAL_SECONDS = 30
REDIS_SETTINGS = RedisSettings.from_dsn(get_settings().redis_url)

# Every worker process imports this module: scrub its logs (ARQ logs a failed
# job's exception text and traceback, which may carry a provider URL).
install_log_safety()
logger = logging.getLogger(__name__)


def _parse_cron_hour_minute(expr: str) -> tuple[int, int]:
    """Parse the minute/hour fields of a 5-field cron expression (default 04:00)."""
    parts = expr.split()
    try:
        minute = int(parts[0])
        hour = int(parts[1])
        return hour, minute
    except (IndexError, ValueError):
        return 4, 0


_REEVAL_CRON = "0 4 * * *"
_hour, _minute = _parse_cron_hour_minute(_REEVAL_CRON)


def _check_queue(coroutine: WorkerCoroutine, queue: Queue) -> str:
    # Task functions are module-level, so __qualname__ is the registered name.
    name = coroutine.__qualname__
    if queue_for(name) is not queue:
        raise ValueError(f"{name} is mapped to {queue_for(name)}, not {queue}")
    return name


def _task(coroutine: WorkerCoroutine, queue: Queue, **options: Any) -> Function:
    """Register ``coroutine`` on ``queue``, refusing a mismatch with the mapping."""
    return func(coroutine, name=_check_queue(coroutine, queue), **options)


def _cron(coroutine: WorkerCoroutine, queue: Queue, **schedule: Any) -> CronJob:
    """Schedule ``coroutine`` on the worker that serves its mapped queue."""
    _check_queue(coroutine, queue)
    return cron(coroutine, **schedule)


async def _bootstrap_live_loop(ctx: dict[str, Any]) -> None:
    """Kick off the self-rescheduling live poll when a realtime worker starts.
    Overlapping chains are kept from polling concurrently by the poll's Redis
    single-flight lock (``LIVE_POLL_LOCK_KEY``). Without a live provider key the
    loop is not started, and this is the one place that says so."""
    if not live_provider_configured(get_settings()):
        logger.warning(
            "Live polling is disabled: API_FOOTBALL_KEY is not set. "
            "Set it and restart worker-realtime to enable it."
        )
        return
    await enqueue(ctx["redis"], "poll_live_task")


class WorkerSettings(Protocol):
    """Shape of a worker settings class (typing only).

    The `arq` CLI reads ``settings_cls.__dict__``, i.e. only attributes defined
    on the class itself — inherited ones are silently ignored. Every worker
    class therefore spells out all of its settings instead of sharing a base.
    """

    redis_settings: RedisSettings
    health_check_interval: int
    queue_name: Queue
    functions: list[Function]
    cron_jobs: list[CronJob]


class RealtimeWorker:
    """Short, latency-sensitive jobs. In-play recompute is a ~20 µs Dixon-Coles
    evaluation plus DB I/O, so it stays on the event loop."""

    redis_settings = REDIS_SETTINGS
    health_check_interval = HEALTH_CHECK_INTERVAL_SECONDS
    queue_name = Queue.REALTIME
    max_jobs = 20
    job_timeout = 60
    functions = [
        # Retrying would start a second self-rescheduling chain: the failed run
        # already re-enqueued itself in its `finally` block.
        _task(poll_live_task, Queue.REALTIME, max_tries=1),
        # Idempotent: an unchanged (minute, score) state is a no-op.
        _task(recompute_fixture_task, Queue.REALTIME, max_tries=3),
        # Delivery retries once on its own; an ARQ retry would double-send.
        _task(push_task, Queue.REALTIME, max_tries=1),
    ]
    cron_jobs: list[CronJob] = []
    on_startup = _bootstrap_live_loop


class BatchWorker:
    """Admin-triggered historical re-scans and the midnight LLM ranking."""

    redis_settings = REDIS_SETTINGS
    health_check_interval = HEALTH_CHECK_INTERVAL_SECONDS
    queue_name = Queue.BATCH
    max_jobs = 2
    job_timeout = int(timedelta(minutes=30).total_seconds())
    functions = [
        # Ingestion upserts are idempotent; failed re-scans are re-run from the admin UI.
        _task(ingest_history_task, Queue.BATCH, max_tries=1),
        _task(rank_llm_fixtures_task, Queue.BATCH, max_tries=1, timeout=600),
    ]
    cron_jobs = [
        # LLM-analysis ranking of today's fixtures, recomputed at UTC midnight.
        _cron(rank_llm_fixtures_task, Queue.BATCH, hour=0, minute=0),
    ]


class MlWorker:
    """CPU-bound model work, one job at a time. Training blocks this worker's
    event loop, which is why it never shares a process with the other queues;
    run it on a dedicated host by pointing it at the same Redis/Postgres/S3."""

    redis_settings = REDIS_SETTINGS
    health_check_interval = HEALTH_CHECK_INTERVAL_SECONDS
    queue_name = Queue.ML
    max_jobs = 1
    job_timeout = int(timedelta(hours=2).total_seconds())
    functions = [
        _task(train_all_task, Queue.ML, max_tries=1),
        _task(
            reevaluate_champions_task,
            Queue.ML,
            max_tries=1,
            timeout=get_settings().champion_reeval_max_runtime_seconds,
        ),
    ]
    cron_jobs = [
        _cron(reevaluate_champions_task, Queue.ML, hour=_hour, minute=_minute),
    ]


WORKERS: tuple[type[WorkerSettings], ...] = (RealtimeWorker, BatchWorker, MlWorker)
