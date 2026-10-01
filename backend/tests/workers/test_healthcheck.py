"""Worker container healthcheck (python -m app.workers.healthcheck <queue>)."""

from __future__ import annotations

import pytest
from app.core.config import get_settings
from app.workers.healthcheck import main
from app.workers.queues import Queue
from arq.constants import health_check_key_suffix
from redis import Redis


def test_healthy_only_while_the_worker_heartbeat_key_exists() -> None:
    with Redis.from_url(get_settings().redis_url) as redis:
        assert main(["realtime"]) == 1

        redis.set(Queue.REALTIME + health_check_key_suffix, "j_complete=0", px=5_000)
        assert main(["realtime"]) == 0
        # Another queue's heartbeat does not count.
        assert main(["ml"]) == 1


@pytest.mark.parametrize("argv", [[], ["live"], ["realtime", "ml"]])
def test_rejects_unknown_queues(argv: list[str]) -> None:
    assert main(argv) == 2
