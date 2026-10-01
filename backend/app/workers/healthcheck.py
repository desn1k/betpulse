"""Container healthcheck for an ARQ worker: ``python -m app.workers.healthcheck <queue>``.

Every worker refreshes ``<queue_name>:health-check`` in Redis each
``health_check_interval`` seconds (ARQ expires it shortly after). Checking the
key directly keeps the probe cheap — ``arq --check`` would import the worker
settings module and with it the whole ML stack on every probe.
"""

from __future__ import annotations

import sys

from arq.constants import health_check_key_suffix
from redis import Redis

from app.core.config import get_settings
from app.workers.queues import Queue


def is_healthy(queue: Queue, redis: Redis) -> bool:
    return bool(redis.exists(queue + health_check_key_suffix))


QUEUES_BY_NAME = {queue.name.lower(): queue for queue in Queue}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in QUEUES_BY_NAME:
        print(
            f"usage: python -m app.workers.healthcheck <{'|'.join(QUEUES_BY_NAME)}>",
            file=sys.stderr,
        )
        return 2
    with Redis.from_url(get_settings().redis_url) as redis:
        return 0 if is_healthy(QUEUES_BY_NAME[argv[0]], redis) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
