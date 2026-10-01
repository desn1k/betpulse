"""Queue isolation against real Redis with arq workers in burst mode.

Stub coroutines are registered under the real task names, so jobs are routed by
the production mapping (``app.workers.queues.enqueue``) without running the
real task bodies.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from app.core.config import get_settings
from app.workers.arq_app import MlWorker, RealtimeWorker
from app.workers.queues import Queue, enqueue
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.constants import in_progress_key_prefix
from arq.worker import Worker, func

TRAIN_SECONDS = 6


async def _stub(ctx: dict[str, Any], *args: Any) -> str:
    return "done"


@pytest_asyncio.fixture
async def pool() -> AsyncIterator[ArqRedis]:
    redis = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        yield redis
    finally:
        await redis.aclose()


def _burst_worker(pool: ArqRedis, queue: str, *task_names: str) -> Worker:
    return Worker(
        functions=[func(_stub, name=name) for name in task_names],
        queue_name=queue,
        redis_pool=pool,
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
        max_tries=1,
    )


async def test_jobs_only_run_on_the_worker_bound_to_their_queue(pool: ArqRedis) -> None:
    live_job = await enqueue(pool, "push_task", "fixture", "text")
    train_job = await enqueue(pool, "train_all_task")
    assert live_job is not None and train_job is not None

    # A worker bound to the ml queue never touches the realtime job, and vice versa.
    ml_worker = _burst_worker(pool, MlWorker.queue_name, "push_task", "train_all_task")
    await ml_worker.main()
    assert ml_worker.jobs_complete == 1
    assert await train_job.result(timeout=1) == "done"
    assert [j.function for j in await pool.queued_jobs(queue_name=Queue.REALTIME)] == ["push_task"]

    realtime_worker = _burst_worker(pool, RealtimeWorker.queue_name, "push_task", "train_all_task")
    await realtime_worker.main()
    assert realtime_worker.jobs_complete == 1
    assert await live_job.result(timeout=1) == "done"


# A separate process stands in for worker-ml: its "training" blocks the event
# loop exactly like CPU-bound LightGBM fitting does.
_BLOCKING_ML_WORKER = textwrap.dedent(
    f"""
    import asyncio, logging, sys, time
    from arq.worker import Worker, func
    from arq.connections import RedisSettings
    from app.core.config import get_settings
    from app.workers.arq_app import MlWorker

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    async def train_all_task(ctx):
        time.sleep({TRAIN_SECONDS})
        return "trained"

    worker = Worker(
        functions=[func(train_all_task, name="train_all_task")],
        queue_name=MlWorker.queue_name,
        redis_settings=RedisSettings.from_dsn(get_settings().redis_url),
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
    )
    asyncio.run(worker.main())
    print("ml worker drained its queue", file=sys.stderr, flush=True)
    """
)


async def test_long_training_does_not_block_live_jobs(pool: ArqRedis, tmp_path: Path) -> None:
    train_job = await enqueue(pool, "train_all_task")
    assert train_job is not None

    log_path = tmp_path / "ml-worker.log"
    with log_path.open("wb") as log:
        # Started off the event loop; polled with the non-blocking Popen.poll().
        ml_process = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-c", _BLOCKING_ML_WORKER],
            env=os.environ.copy(),
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    def worker_log() -> str:
        return log_path.read_text(encoding="utf-8", errors="replace")

    try:
        deadline = time.monotonic() + 30
        while not await pool.exists(in_progress_key_prefix + train_job.job_id):
            assert time.monotonic() < deadline, f"ml worker never started:\n{worker_log()}"
            assert ml_process.poll() is None, f"ml worker exited early:\n{worker_log()}"
            await asyncio.sleep(0.05)

        started = time.monotonic()
        live_job = await enqueue(pool, "recompute_fixture_task", "fixture", 63, 1, 0)
        assert live_job is not None
        realtime_worker = _burst_worker(pool, RealtimeWorker.queue_name, "recompute_fixture_task")
        await realtime_worker.main()
        elapsed = time.monotonic() - started

        assert await live_job.result(timeout=1) == "done"
        assert elapsed < TRAIN_SECONDS / 2, f"live job waited {elapsed:.1f}s behind training"
        # Training was still running in the other process while the live job completed.
        assert await pool.exists(in_progress_key_prefix + train_job.job_id)
        assert ml_process.poll() is None

        # ...and it still finishes normally.
        assert await train_job.result(timeout=TRAIN_SECONDS + 15) == "trained", worker_log()
    finally:
        try:
            await asyncio.to_thread(ml_process.wait, 15)
        except subprocess.TimeoutExpired:
            ml_process.kill()
            await asyncio.to_thread(ml_process.wait)
            raise AssertionError(
                f"ml worker did not exit after draining:\n{worker_log()}"
            ) from None

    assert ml_process.returncode == 0, worker_log()
