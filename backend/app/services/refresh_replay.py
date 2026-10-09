"""Idempotent replay of a just-rotated refresh token (F13 part B).

When a rotation T1 → T2 commits but its answer never reaches the browser (a
dropped response, a closed tab), the browser presents T1 again. Without help
that is reuse: a 409 inside ``REFRESH_REUSE_GRACE_SECONDS``, family revocation
after. So each rotation leaves an entry here, keyed by T1's hash, holding T2
Fernet-encrypted plus a fingerprint of the rotating request (user-agent HMAC,
client subnet) and the account's ``credentials_changed_at``. It lives for
``REFRESH_REPLAY_WINDOW_SECONDS``.

The decision is made in :mod:`app.services.auth` under the family lock, against
the database (T2 unused, the window on the DB clock, the account); this module
only stores, loads and counts. Redis is best effort: a failure or a slow answer
(bounded by ``_REDIS_TIMEOUT_SECONDS``) never fails a rotation — the replay is
just not available and the ordinary rotation rules apply.

The entry is a hash: ``data`` (the encrypted entry) and ``uses`` (successful
replays, capped by ``REFRESH_REPLAY_MAX_USES``, counted atomically in Lua).
Successful replays are also limited per client IP; a refused one counts nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.client_ip import rate_limit_bucket, replay_subnet
from app.core.config import get_settings
from app.core.crypto import SecretEncryptionError, decrypt_secret, encrypt_secret
from app.services.counters import incr_within_limit

logger = logging.getLogger(__name__)

KEY_PREFIX = "auth:refresh_replay:"
_REDIS_TIMEOUT_SECONDS = 0.5
_STORE_ERRORS = (RedisError, OSError, TimeoutError)

# Store the entry with a zero use count and its TTL in one step.
_REMEMBER = """
redis.call('DEL', KEYS[1])
redis.call('HSET', KEYS[1], 'data', ARGV[1], 'uses', 0)
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""

_LOAD = "return redis.call('HGET', KEYS[1], 'data')"

# Take one use of an entry. -1: no entry (expired); -2: the cap is reached
# (nothing counted); otherwise the use count after taking it.
_CLAIM = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return -1
end
local uses = redis.call('HINCRBY', KEYS[1], 'uses', 1)
if uses > tonumber(ARGV[1]) then
  redis.call('HINCRBY', KEYS[1], 'uses', -1)
  return -2
end
return uses
"""

# Give back a use taken by _CLAIM; never below zero, never recreates the key.
_RELEASE = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return 0
end
if tonumber(redis.call('HGET', KEYS[1], 'uses') or '0') <= 0 then
  return 0
end
return redis.call('HINCRBY', KEYS[1], 'uses', -1)
"""


@dataclass(frozen=True, slots=True)
class ReplayEntry:
    token: str  # T2, plaintext — only ever inside the encrypted blob
    token_id: uuid.UUID
    ua: str  # user-agent fingerprint of the rotating request
    subnet: str  # client subnet of the rotating request
    cca: str | None  # the account's credentials_changed_at at rotation


def enabled() -> bool:
    return get_settings().refresh_replay_window_seconds > 0


def _key(presented_hash: str) -> str:
    return KEY_PREFIX + presented_hash


def user_agent_fingerprint(user_agent: str | None) -> str | None:
    """Keyed hash of the user-agent; ``None`` for a missing or empty one, which
    never matches anything."""
    if not user_agent:
        return None
    secret = get_settings().secret_key.encode("utf-8")
    return hmac.new(
        secret, b"refresh-replay-ua:" + user_agent.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def credentials_stamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def _call(redis: Redis, script: str, keys: list[str], args: list[Any]) -> Any:
    async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
        return await redis.register_script(script)(keys=keys, args=args)


async def remember(
    redis: Redis,
    *,
    presented_hash: str,
    token: str,
    token_id: uuid.UUID,
    user_agent: str | None,
    ip: str | None,
    credentials_changed_at: datetime | None,
) -> None:
    """Store T2 for a replay of the token hashed as ``presented_hash``. Nothing
    is stored without a user-agent and a known client address (nothing could
    match), and a Redis failure is logged, never raised."""
    window = get_settings().refresh_replay_window_seconds
    ua = user_agent_fingerprint(user_agent)
    subnet = replay_subnet(ip) if ip else None
    if window <= 0 or ua is None or subnet is None:
        return
    blob = encrypt_secret(
        json.dumps(
            {
                "token": token,
                "token_id": str(token_id),
                "ua": ua,
                "subnet": subnet,
                "cca": credentials_stamp(credentials_changed_at),
            }
        )
    )
    try:
        await _call(redis, _REMEMBER, [_key(presented_hash)], [blob, window])
    except _STORE_ERRORS as exc:
        logger.warning("refresh replay entry not stored: %s", type(exc).__name__)


async def load(redis: Redis, presented_hash: str) -> ReplayEntry | str:
    """The entry for a rotated token, or why there is none: ``no_entry``,
    ``store_unavailable`` or ``entry_invalid``."""
    try:
        blob = await _call(redis, _LOAD, [_key(presented_hash)], [])
    except _STORE_ERRORS as exc:
        logger.warning("refresh replay entry not read: %s", type(exc).__name__)
        return "store_unavailable"
    if blob is None:
        return "no_entry"
    try:
        data = json.loads(decrypt_secret(str(blob)))
        return ReplayEntry(
            token=str(data["token"]),
            token_id=uuid.UUID(str(data["token_id"])),
            ua=str(data["ua"]),
            subnet=str(data["subnet"]),
            cca=None if data["cca"] is None else str(data["cca"]),
        )
    except (SecretEncryptionError, ValueError, KeyError, TypeError):
        return "entry_invalid"


async def claim(redis: Redis, presented_hash: str, *, ip: str) -> int | str:
    """Take one replay of the entry and one unit of the client's per-IP budget.
    Returns the entry's use count, or why not: ``no_entry``, ``cap_reached``,
    ``ip_limited``, ``store_unavailable``. A refusal counts nothing."""
    settings = get_settings()
    key = _key(presented_hash)
    try:
        uses = int(await _call(redis, _CLAIM, [key], [settings.refresh_replay_max_uses]))
        if uses == -1:
            return "no_entry"
        if uses == -2:
            return "cap_reached"
        async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
            within = await incr_within_limit(
                redis,
                f"rl:refresh_replay:ip:{rate_limit_bucket(ip)}",
                limit=settings.rate_limit_refresh_replay_per_minute,
                ttl_seconds=60,
            )
        if within is None:
            await _call(redis, _RELEASE, [key], [])
            return "ip_limited"
    except _STORE_ERRORS as exc:
        logger.warning("refresh replay not claimed: %s", type(exc).__name__)
        return "store_unavailable"
    return uses
