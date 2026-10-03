"""Guard: the configured connection pools fit inside Postgres ``max_connections``.

Reads only what the budget needs from the compose files (base + prod overlay):
each service's ``environment``, ``command`` and ``deploy.replicas``. Pool
defaults come from :class:`app.core.config.Settings`, so changing either side
re-runs the arithmetic. See HANDOFF "Database connection budget".
"""

from __future__ import annotations

import re
import textwrap
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


class _Reset:
    """A compose ``!reset`` value: the attribute is cleared in the merged model."""


class _Override:
    """A compose ``!override`` value: the attribute replaces, never merges."""

    def __init__(self, value: Any) -> None:
        self.value = value


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that understands compose's merge tags (``!reset``, ``!override``)."""


def _construct_reset(loader: yaml.SafeLoader, node: yaml.Node) -> _Reset:
    return _Reset()


def _construct_override(loader: yaml.SafeLoader, node: yaml.Node) -> _Override:
    if isinstance(node, yaml.MappingNode):
        return _Override(loader.construct_mapping(node, deep=True))
    if isinstance(node, yaml.SequenceNode):
        return _Override(loader.construct_sequence(node, deep=True))
    return _Override(loader.construct_scalar(node))  # type: ignore[arg-type]


_ComposeLoader.add_constructor("!reset", _construct_reset)
_ComposeLoader.add_constructor("!override", _construct_override)


def _load_compose(text: str) -> Any:
    loader = _ComposeLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def _command_text(command: Any) -> str:
    if command is None:
        return ""
    return " ".join(map(str, command)) if isinstance(command, list) else str(command)


def _merge_docs(docs: list[Any]) -> dict[str, dict[str, Any]]:
    """Per service, the three fields the budget reads, merged across parsed
    compose documents (later documents win; ``environment`` merges key by key,
    as compose does; ``!reset`` clears a field and ``!override`` replaces it,
    even with an empty value)."""
    merged: dict[str, dict[str, Any]] = {}
    for doc in docs:
        for service, spec in ((doc or {}).get("services") or {}).items():
            entry = merged.setdefault(service, {"environment": {}, "command": "", "replicas": 1})
            spec = spec or {}
            env = spec.get("environment") or {}
            if isinstance(env, _Reset):
                entry["environment"], env = {}, {}
            elif isinstance(env, _Override):
                entry["environment"], env = {}, env.value or {}
            if isinstance(env, list):
                env = dict(item.split("=", 1) for item in env)
            entry["environment"].update({k: str(v) for k, v in env.items()})
            command = spec.get("command")
            if isinstance(command, _Reset):
                entry["command"] = ""
            elif isinstance(command, _Override):
                entry["command"] = _command_text(command.value)
            elif command:
                entry["command"] = _command_text(command)
            replicas = (spec.get("deploy") or {}).get("replicas")
            if replicas is not None:
                entry["replicas"] = int(replicas)
    return merged


def _merged_services(files: list[str]) -> dict[str, dict[str, Any]]:
    docs = [_load_compose((INFRA / name).read_text(encoding="utf-8")) for name in files]
    return _merge_docs(docs)


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


def test_loader_understands_compose_merge_tags() -> None:
    """The prod overlay clears ports with ``!reset`` (a plain ``[]`` would be
    appended); the budget must still parse it, and apply both tags."""
    doc = _load_compose(
        "services:\n"
        "  a:\n"
        "    ports: !reset []\n"
        "    environment: !override {DB_POOL_SIZE: '1'}\n"
        "    command: !reset null\n",
    )
    service = doc["services"]["a"]
    assert isinstance(service["ports"], _Reset)
    assert isinstance(service["command"], _Reset)
    assert isinstance(service["environment"], _Override)
    assert service["environment"].value == {"DB_POOL_SIZE": "1"}


@pytest.mark.parametrize("empty", ["[]", '""'])
def test_merge_applies_reset_and_empty_override(empty: str) -> None:
    base = _load_compose(
        textwrap.dedent(
            """
            services:
              a:
                command: [postgres, -c, max_connections=120]
                environment: {DB_POOL_SIZE: '5', DB_MAX_OVERFLOW: '10'}
              b:
                command: arq app.workers.arq_app.MlWorker
            """
        )
    )
    overlay = _load_compose(
        textwrap.dedent(
            f"""
            services:
              a:
                command: !override {empty}
                environment: !override {{DB_POOL_SIZE: '1'}}
              b:
                command: !reset null
            """
        )
    )
    merged = _merge_docs([base, overlay])
    assert merged["a"]["command"] == ""  # an empty !override still replaces
    assert merged["a"]["environment"] == {"DB_POOL_SIZE": "1"}
    assert merged["b"]["command"] == ""
    # Plain values still merge as compose does.
    extra = _load_compose(
        textwrap.dedent(
            """
            services:
              a:
                environment: {X: '1'}
            """
        )
    )
    plain = _merge_docs([base, extra])
    assert plain["a"]["command"] == "postgres -c max_connections=120"
    assert plain["a"]["environment"] == {"DB_POOL_SIZE": "5", "DB_MAX_OVERFLOW": "10", "X": "1"}
