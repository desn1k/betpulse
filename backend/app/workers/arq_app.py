"""ARQ worker + scheduler configuration.

Runs the ``train`` queue and the nightly champion re-evaluation cron. Exactly
one scheduler should run in production; the cron task itself takes a Redis lock
(with a TTL) so a second instance is a safe hot standby (spec §18).
"""

from __future__ import annotations

from arq.connections import RedisSettings
from arq.cron import cron

from app.core.config import get_settings
from app.workers.tasks import reevaluate_champions_task, train_all_task


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


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    functions = [train_all_task, reevaluate_champions_task]
    cron_jobs = [cron(reevaluate_champions_task, hour=_hour, minute=_minute)]
    queue_name = "train"
