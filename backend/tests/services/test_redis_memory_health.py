"""System health component ``redis_memory``: Redis used memory against
``maxmemory``. Production runs ``noeviction`` (quota, rate-limit and ARQ keys all
carry a TTL, so no eviction policy can spare them): a full Redis refuses writes,
so the admin page turns degraded above 80 % to show it before that happens."""

from __future__ import annotations

from typing import Any

import pytest
from app.core.redis import get_redis
from app.services.system_health import REDIS_MEMORY_DEGRADED_RATIO, check_redis_memory

MB = 1024 * 1024


class _FakeRedis:
    def __init__(self, info: dict[str, Any] | Exception) -> None:
        self._info = info
        self.sections: list[str] = []

    async def info(self, section: str) -> dict[str, Any]:
        self.sections.append(section)
        if isinstance(self._info, Exception):
            raise self._info
        return self._info


def _memory(used_mb: float, max_mb: float, policy: str = "noeviction") -> dict[str, Any]:
    return {
        "used_memory": int(used_mb * MB),
        "maxmemory": int(max_mb * MB),
        "maxmemory_policy": policy,
    }


def test_threshold_is_80_percent() -> None:
    assert REDIS_MEMORY_DEGRADED_RATIO == 0.8


async def test_ok_below_the_threshold() -> None:
    redis = _FakeRedis(_memory(100, 256))
    component = await check_redis_memory(redis)  # type: ignore[arg-type]
    assert redis.sections == ["memory"]
    assert component.name == "redis_memory"
    assert component.status == "ok"
    assert component.meta == {
        "used_bytes": 100 * MB,
        "maxmemory_bytes": 256 * MB,
        "used_percent": 39.1,
        "policy": "noeviction",
    }
    assert "39.1%" in (component.detail or "")


@pytest.mark.parametrize(
    ("used_mb", "status"), [(204.7, "ok"), (204.9, "degraded"), (256, "degraded")]
)
async def test_degraded_above_80_percent(used_mb: float, status: str) -> None:
    component = await check_redis_memory(_FakeRedis(_memory(used_mb, 256)))  # type: ignore[arg-type]
    assert component.status == status
    if status == "degraded":
        assert "refuses writes" in (component.detail or "")


async def test_no_maxmemory_is_not_configured() -> None:
    component = await check_redis_memory(_FakeRedis(_memory(3, 0)))  # type: ignore[arg-type]
    assert component.status == "not_configured"
    assert "maxmemory" in (component.detail or "")
    assert component.meta["maxmemory_bytes"] == 0


async def test_an_evicting_policy_is_degraded_even_with_room() -> None:
    redis = _FakeRedis(_memory(10, 256, policy="volatile-lru"))
    component = await check_redis_memory(redis)  # type: ignore[arg-type]
    assert component.status == "degraded"
    assert "volatile-lru" in (component.detail or "")
    assert "noeviction" in (component.detail or "")


async def test_an_unreadable_info_is_degraded_without_raising() -> None:
    component = await check_redis_memory(_FakeRedis(ConnectionError("down")))  # type: ignore[arg-type]
    assert component.status == "degraded"
    assert component.detail == "ConnectionError"


async def test_reads_a_real_redis() -> None:
    component = await check_redis_memory(get_redis())
    assert component.name == "redis_memory"
    assert component.meta["used_bytes"] > 0
    assert "policy" in component.meta
