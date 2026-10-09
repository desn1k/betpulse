"""Client requests that arrive as one of our own addresses (F5 guard).

A client identity inside ``INTERNAL_NETWORK_CIDRS`` is never a real client: it
is the Docker bridge gateway (Docker's userland proxy relayed the connection,
which happens to IPv6 clients when the Compose network has no IPv6) or an
internal hop the API does not trust. Every client arriving that way shares one
identity for per-IP rate limits, guest quotas and the refresh replay's subnet
binding, so the API must never let it pass silently:

* each API process logs one warning on its first sighting;
* sightings are recorded in Redis (count, first and last time, last address and
  path; kept 24 h after the last one), written at most once a minute per
  process, so every API process's admin sees them on the system health page.

Nothing here may break a request: a Redis failure is logged and dropped.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import cast

from redis.asyncio import Redis

from app.core.config import Settings
from app.schemas.system import ComponentHealth

logger = logging.getLogger(__name__)

COLLAPSE_KEY = "ops:client_identity:internal"
RECORD_TTL_SECONDS = 24 * 3600
FLUSH_INTERVAL_SECONDS = 60.0


class CollapseWatch:
    """Per-process state: whether the warning was logged, and the sightings
    not yet written to Redis."""

    def __init__(self) -> None:
        self._warned = False
        self._pending = 0
        self._last_flush: float | None = None
        self._last_client = ""
        self._last_path = ""

    async def observe(
        self, redis: Redis, client: str, path: str, *, now: float | None = None
    ) -> None:
        if not self._warned:
            self._warned = True
            logger.warning(
                "Client requests arrive as %s, an address inside our own Docker network "
                "(INTERNAL_NETWORK_CIDRS), first on %s: every client arriving this way shares "
                "one identity for rate limits, guest quotas and the refresh replay binding. "
                "Check the network's IPv6 setup and TRUSTED_PROXY_CIDRS (HANDOFF F5, "
                "docs/DEPLOY_VPS.md); a request sent from the server itself to "
                "https://localhost also arrives this way. Logged once per process; the admin "
                "system health page shows the count.",
                client,
                path,
            )
        self._pending += 1
        self._last_client, self._last_path = client, path
        moment = time.monotonic() if now is None else now
        if self._last_flush is not None and moment - self._last_flush < FLUSH_INTERVAL_SECONDS:
            return
        self._last_flush = moment
        await self._flush(redis)

    async def _flush(self, redis: Redis) -> None:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        pending, self._pending = self._pending, 0
        try:
            pipe = redis.pipeline(transaction=True)
            pipe.hsetnx(COLLAPSE_KEY, "first_seen", stamp)
            pipe.hset(
                COLLAPSE_KEY,
                mapping={
                    "last_seen": stamp,
                    "last_client": self._last_client,
                    "last_path": self._last_path,
                },
            )
            pipe.hincrby(COLLAPSE_KEY, "count", pending)
            pipe.expire(COLLAPSE_KEY, RECORD_TTL_SECONDS)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - never break a request over this
            self._pending += pending
            logger.debug("could not record an internal client identity: %s", exc)


_watch = CollapseWatch()


async def observe(redis: Redis, client: str, path: str) -> None:
    """Record a request whose client identity is one of our own addresses."""
    await _watch.observe(redis, client, path)


def reset_watch() -> None:
    """Fresh per-process state (tests)."""
    global _watch
    _watch = CollapseWatch()


async def read_record(redis: Redis) -> dict[str, str]:
    """The recorded sightings (empty when none in the last 24 h)."""
    return await cast(Awaitable[dict[str, str]], redis.hgetall(COLLAPSE_KEY))


async def check_client_identity(redis: Redis, settings: Settings) -> ComponentHealth:
    """System health component ``client_ip``: degraded while a sighting is
    recorded (24 h after the last one)."""
    name = "client_ip"
    if not settings.internal_networks:
        return ComponentHealth(
            name=name,
            status="not_configured",
            detail="INTERNAL_NETWORK_CIDRS is not set; the check is off",
        )
    try:
        record = await read_record(redis)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return ComponentHealth(name=name, status="degraded", detail=exc.__class__.__name__)
    if not record:
        return ComponentHealth(
            name=name,
            status="ok",
            detail="no client arrived as an internal address in the last 24 h",
        )
    count = int(record.get("count", "0"))
    return ComponentHealth(
        name=name,
        status="degraded",
        detail=(
            f"{count} client request(s) since {record.get('first_seen')} arrived as "
            f"{record.get('last_client')}, an address inside our own network (last "
            f"{record.get('last_seen')} on {record.get('last_path')}): those clients share "
            "one identity. See HANDOFF F5 and docs/DEPLOY_VPS.md."
        ),
        meta={
            "count": count,
            "first_seen": record.get("first_seen"),
            "last_seen": record.get("last_seen"),
            "last_client": record.get("last_client"),
            "last_path": record.get("last_path"),
        },
    )
