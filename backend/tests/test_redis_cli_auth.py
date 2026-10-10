"""Every ``redis-cli`` the operator scripts and Compose files run authenticates.

Production Redis requires a password (infra/docker-compose*.yml), and
``redis-cli`` answers ``NOAUTH`` with exit status 0, so a call without the
password fails quietly: a healthcheck reports healthy, a diagnostic finds
nothing. A call authenticates when its line sets ``REDISCLI_AUTH`` (from the
container's ``REDIS_PASSWORD``, so the password never leaves the container) or
goes through ``redis_cli`` from ``scripts/redis-persistence.sh``. Test scripts
are exempt: they start password-less Redis on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CALL = re.compile(r"\bredis-cli\b")


def _sources() -> list[Path]:
    scripts = sorted((REPO / "scripts").glob("*.sh"))
    compose = sorted((REPO / "infra").glob("docker-compose*.yml"))
    return scripts + compose


def _unauthenticated_calls() -> list[str]:
    found = []
    for path in _sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0] if path.suffix == ".sh" else line
            if path.suffix == ".yml" and line.lstrip().startswith("#"):
                continue
            if code.strip().startswith("echo "):  # a message naming redis-cli
                continue
            if CALL.search(code) and "REDISCLI_AUTH" not in code:
                found.append(f"{path.relative_to(REPO).as_posix()}:{number}: {line.strip()}")
    return found


def test_sources_are_scanned() -> None:
    names = {p.name for p in _sources()}
    assert {"diagnose-client-ip.sh", "redis-persistence.sh", "docker-compose.yml"} <= names


def test_every_redis_cli_call_authenticates() -> None:
    assert _unauthenticated_calls() == []
