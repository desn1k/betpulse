"""ARQ queue names and the task → queue mapping (spec §18).

The single source of truth for where every background task runs. ARQ binds
one queue per worker process, so each queue is served by its own Compose
service (see ``app.workers.arq_app``):

- ``realtime`` — live poll → in-play recompute → push. Short, I/O-bound jobs
  that must never wait behind anything else.
- ``batch`` — historical ingestion re-scans and the LLM fixture ranking.
- ``ml`` — model training and the nightly champion re-evaluation. Training is
  CPU-bound and blocks its worker's event loop for minutes, so it gets a
  process (and optionally a machine) of its own.

The spec sketched five queues (``ingest``, ``train``, ``live``, ``push``,
``llm``); three are enough for its goal — isolating training from live
recomputation — without five worker processes. Splitting a task out later is
one mapping entry plus one worker class.

Enqueue only through :func:`enqueue`; a test fails on any other
``enqueue_job`` call in ``app/``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from arq.connections import ArqRedis
from arq.jobs import Job


class Queue(StrEnum):
    REALTIME = "arq:queue:realtime"
    BATCH = "arq:queue:batch"
    ML = "arq:queue:ml"


TASK_QUEUES: dict[str, Queue] = {
    "poll_live_task": Queue.REALTIME,
    "recompute_fixture_task": Queue.REALTIME,
    "push_task": Queue.REALTIME,
    "ingest_history_task": Queue.BATCH,
    "rank_llm_fixtures_task": Queue.BATCH,
    "train_all_task": Queue.ML,
    "reevaluate_champions_task": Queue.ML,
}


def queue_for(task: str) -> Queue:
    """The queue a task runs on. Unknown task names raise ``KeyError``."""
    return TASK_QUEUES[task]


async def enqueue(pool: ArqRedis, task: str, *args: Any, **kwargs: Any) -> Job | None:
    """Enqueue ``task`` on its mapped queue. ``kwargs`` may carry ARQ's
    ``_defer_by`` / ``_job_id`` options as well as task keyword arguments."""
    return await pool.enqueue_job(task, *args, _queue_name=queue_for(task), **kwargs)
