"""Queue topology guards: every task runs on exactly one worker of its mapped
queue, crons are scheduled once, and nothing enqueues around the mapping."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from app.workers import tasks
from app.workers.arq_app import (
    WORKERS,
    BatchWorker,
    MlWorker,
    RealtimeWorker,
    WorkerSettingsBase,
)
from app.workers.queues import TASK_QUEUES, Queue

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
QUEUES_MODULE = APP_ROOT / "workers" / "queues.py"


def _function_names(worker: type[WorkerSettingsBase]) -> list[str]:
    return [f.name for f in worker.functions]


def _cron_names(worker: type[WorkerSettingsBase]) -> list[str]:
    return [c.name for c in worker.cron_jobs]


def test_each_queue_has_exactly_one_worker() -> None:
    assert sorted(w.queue_name for w in WORKERS) == sorted(Queue)


def test_every_task_is_registered_on_exactly_one_worker_of_its_queue() -> None:
    for task, queue in TASK_QUEUES.items():
        owners = [w for w in WORKERS if task in _function_names(w)]
        assert len(owners) == 1, f"{task} registered on {owners}"
        assert owners[0].queue_name == queue

    registered = [name for w in WORKERS for name in _function_names(w)]
    assert sorted(registered) == sorted(TASK_QUEUES)


def test_every_task_function_has_a_queue() -> None:
    task_functions = {
        name
        for name, fn in inspect.getmembers(tasks, inspect.iscoroutinefunction)
        if name.endswith("_task") and fn.__module__ == tasks.__name__
    }
    assert task_functions == set(TASK_QUEUES)


def test_crons_run_once_on_their_queue() -> None:
    crons = {w: _cron_names(w) for w in WORKERS}
    assert crons[RealtimeWorker] == []
    assert crons[BatchWorker] == ["cron:rank_llm_fixtures_task"]
    assert crons[MlWorker] == ["cron:reevaluate_champions_task"]


def test_only_the_realtime_worker_bootstraps_the_live_loop() -> None:
    assert getattr(RealtimeWorker, "on_startup", None) is not None
    assert getattr(BatchWorker, "on_startup", None) is None
    assert getattr(MlWorker, "on_startup", None) is None


def test_live_pipeline_tasks_do_not_retry_into_duplicates() -> None:
    options = {f.name: f.max_tries for f in RealtimeWorker.functions}
    assert options["poll_live_task"] == 1
    assert options["push_task"] == 1


def test_nothing_enqueues_around_the_mapping() -> None:
    """All enqueueing goes through app.workers.queues.enqueue with a mapped task name."""
    offenders: list[str] = []
    unknown: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if path == QUEUES_MODULE:
            continue
        source = path.read_text(encoding="utf-8")
        if "enqueue_job(" in source or re.search(r"[\"']arq:queue", source):
            offenders.append(str(path.relative_to(APP_ROOT)))
        for match in re.finditer(r"\benqueue\(\s*[\w.\[\]\"']+,\s*\"(\w+)\"", source):
            if match.group(1) not in TASK_QUEUES:
                unknown.append(f"{path.relative_to(APP_ROOT)}: {match.group(1)}")

    assert offenders == []
    assert unknown == []
