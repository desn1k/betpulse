"""Guard: the configured connection pools fit inside Postgres ``max_connections``.

Reads only what the budget needs from the compose files (base + prod overlay):
each service's ``environment``, ``command`` and ``deploy.replicas``. Pool
defaults come from :class:`app.core.config.Settings`, so changing either side
re-runs the arithmetic. See HANDOFF "Database connection budget".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from app.core.config import Settings

INFRA = Path(__file__).resolve().parents[2] / "infra"
COMPOSE_STACKS = {
    "dev": ["docker-compose.yml"],
    "prod": ["docker-compose.yml", "docker-compose.prod.yml"],
}
# psql sessions, migrations, backups, ad-hoc admin work.
HEADROOM = 10
# Postgres default; counted unless the postgres command overrides it.
DEFAULT_SUPERUSER_RESERVED = 3
# MLflow keeps one engine per metadata store it serves: tracking and model
# registry (the jobs/workspace stores are disabled in our deployment).
MLFLOW_STORES = 2
# Services that must exist: a rename must not silently drop them from the sum.
EXPECTED = {"api", "worker-realtime", "worker-batch", "worker-ml", "mlflow", "postgres"}


def _merged_services(files: list[str]) -> dict[str, dict[str, Any]]:
    """Per service, the three fields the budget reads, merged across overlays
    (later files win; ``environment`` merges key by key, as compose does)."""
    merged: dict[str, dict[str, Any]] = {}
    for name in files:
        doc = yaml.safe_load((INFRA / name).read_text(encoding="utf-8")) or {}
        for service, spec in (doc.get("services") or {}).items():
            entry = merged.setdefault(service, {"environment": {}, "command": "", "replicas": 1})
            spec = spec or {}
            env = spec.get("environment") or {}
            if isinstance(env, list):
                env = dict(item.split("=", 1) for item in env)
            entry["environment"].update({k: str(v) for k, v in env.items()})
            if spec.get("command"):
                command = spec["command"]
                entry["command"] = " ".join(command) if isinstance(command, list) else command
            replicas = (spec.get("deploy") or {}).get("replicas")
            if replicas is not None:
                entry["replicas"] = int(replicas)
    return merged


def _flag(command: str, pattern: str, default: int) -> int:
    match = re.search(pattern, command)
    return int(match.group(1)) if match else default


def _budget(services: dict[str, dict[str, Any]]) -> tuple[dict[str, int], int]:
    defaults = Settings.model_fields

    def setting(env: dict[str, str], name: str) -> int:
        default = defaults[name].default
        assert isinstance(default, int)
        return int(env.get(name.upper(), default))

    usage: dict[str, int] = {}
    for name, spec in services.items():
        env, command, replicas = spec["environment"], spec["command"], spec["replicas"]
        request_pool = setting(env, "db_pool_size") + setting(env, "db_max_overflow")
        if name == "api":
            security_pool = setting(env, "db_security_pool_size") + setting(
                env, "db_security_max_overflow"
            )
            processes = _flag(command, r"--workers[ =](\d+)", 1)
            usage[name] = replicas * processes * (request_pool + security_pool)
        elif name.startswith("worker-"):
            usage[name] = replicas * request_pool
        elif name == "mlflow":
            # The `mlflow server` parent keeps its pools too, on top of the workers.
            processes = _flag(command, r"--workers[ =](\d+)", 4) + 1
            per_store = int(env.get("MLFLOW_SQLALCHEMYSTORE_POOL_SIZE", 5)) + int(
                env.get("MLFLOW_SQLALCHEMYSTORE_MAX_OVERFLOW", 10)
            )
            usage[name] = replicas * processes * MLFLOW_STORES * per_store

    postgres = services["postgres"]["command"]
    max_connections = _flag(postgres, r"max_connections=(\d+)", 100)
    reserved = _flag(postgres, r"superuser_reserved_connections=(\d+)", DEFAULT_SUPERUSER_RESERVED)
    return usage, max_connections - reserved - HEADROOM


@pytest.mark.parametrize("stack", sorted(COMPOSE_STACKS))
def test_connection_pools_fit_postgres_max_connections(stack: str) -> None:
    services = _merged_services(COMPOSE_STACKS[stack])
    missing = EXPECTED - services.keys()
    assert not missing, f"{stack}: services missing from the budget: {sorted(missing)}"
    assert "max_connections=" in services["postgres"]["command"], (
        f"{stack}: set postgres max_connections explicitly (command: postgres -c ...)"
    )

    usage, budget = _budget(services)
    total = sum(usage.values())
    if total > budget:
        breakdown = ", ".join(
            f"{name}={count}" for name, count in sorted(usage.items(), key=lambda kv: -kv[1])
        )
        pytest.fail(
            f"{stack}: connection pools need {total} > budget {budget} "
            f"(max_connections - superuser_reserved - {HEADROOM} headroom). "
            f"Largest consumers first: {breakdown}. Shrink their pools or raise "
            "max_connections (HANDOFF 'Database connection budget')."
        )


def test_budget_flags_an_oversized_service() -> None:
    """The guard really fails, and names the culprit, when a pool is too big."""
    services = _merged_services(COMPOSE_STACKS["prod"])
    services["worker-batch"]["environment"]["DB_POOL_SIZE"] = "100"
    usage, budget = _budget(services)
    assert sum(usage.values()) > budget
    assert max(usage, key=lambda name: usage[name]) == "worker-batch"


def test_current_budget_numbers() -> None:
    """The per-service numbers HANDOFF documents."""
    usage, budget = _budget(_merged_services(COMPOSE_STACKS["prod"]))
    assert usage == {
        "api": 20,
        "worker-realtime": 15,
        "worker-batch": 5,
        "worker-ml": 5,
        "mlflow": 30,
    }
    assert budget == 120 - 3 - HEADROOM
