"""ARQ worker + scheduler configuration.

Runs the training queue, the nightly champion re-evaluation cron, and the live
pipeline (poll → recompute → push). The live poll is a self-rescheduling task
bootstrapped on startup; exactly one scheduler should run in production, and each
lock-guarded task is a safe hot standby (spec §18).
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings
from arq.cron import cron

from app.core.config import get_settings
from app.workers.tasks import (
    poll_live_task,
    push_task,
    recompute_fixture_task,
    reevaluate_champions_task,
    train_all_task,
)


def _parse_cron_hour_minute(expr: str) -> tuple[int, int]:
    """Parse the minute/hour fields of a 5-field cron expression (default 04:00)."""
    parts = expr.split()
    try:
        minute = int(parts[0])
        hour = int(parts[1])
        return hour, minute
    except (IndexError, ValueError):
        return 4, 0


_RETRAIN_CRON = "0 4 * * *"
_hour, _minute = _parse_cron_hour_minute(_RETRAIN_CRON)


async def _bootstrap_live_loop(ctx: dict[str, Any]) -> None:
    """Kick off the self-rescheduling live poll once when the worker starts."""
    await ctx["redis"].enqueue_job("poll_live_task")


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    functions = [
        train_all_task,
        reevaluate_champions_task,
        poll_live_task,
        recompute_fixture_task,
        push_task,
    ]
    cron_jobs = [cron(reevaluate_champions_task, hour=_hour, minute=_minute)]
    on_startup = _bootstrap_live_loop
