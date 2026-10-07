"""Atomic Redis counters shared by rate limits and usage quotas.

Every operation is a single Lua script, so it runs atomically on the Redis
server: no other command interleaves, and a client crashing mid-way cannot
leave a half-applied update. In particular a counter can never be left without
a TTL — for an un-bucketed key such as the per-IP login window that would be a
permanent lockout. Each script also gives a TTL to a key that somehow lost one
(e.g. a leftover from an older, non-atomic writer), so such keys heal on use.
"""

from __future__ import annotations

from redis.asyncio import Redis

# INCR, then make sure the key expires. Returns the new count.
_INCR_WITH_TTL = """
local count = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

# Check-and-increment against a limit. Returns the new count, or -1 (and leaves
# the count unchanged) when the limit is already reached.
_INCR_WITHIN_LIMIT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= tonumber(ARGV[1]) then
  if redis.call('TTL', KEYS[1]) == -1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
  end
  return -1
end
local count = redis.call('INCR', KEYS[1])
if redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return count
"""

# Check-and-increment that charges each member once: KEYS[1] is the counter,
# KEYS[2] the set of members already charged in the same window. A member
# already in the set costs nothing and returns the current count; otherwise it
# is charged like _INCR_WITHIN_LIMIT and added to the set. A refused member is
# neither counted nor added. Both keys get a TTL if they lack one.
_INCR_DISTINCT_WITHIN_LIMIT = """
local function heal(key)
  if redis.call('TTL', key) == -1 then
    redis.call('EXPIRE', key, ARGV[3])
  end
end
if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then
  heal(KEYS[1])
  heal(KEYS[2])
  return tonumber(redis.call('GET', KEYS[1]) or '0')
end
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= tonumber(ARGV[2]) then
  heal(KEYS[1])
  heal(KEYS[2])
  return -1
end
local count = redis.call('INCR', KEYS[1])
redis.call('SADD', KEYS[2], ARGV[1])
heal(KEYS[1])
heal(KEYS[2])
return count
"""

# Undo one increment, never going below zero. Returns the new count.
_DECR_FLOOR_ZERO = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current <= 0 then
  return 0
end
return redis.call('DECR', KEYS[1])
"""


async def incr_with_ttl(redis: Redis, key: str, ttl_seconds: int) -> int:
    """Increment ``key`` and ensure it expires within ``ttl_seconds`` (a TTL
    already set is kept, so fixed windows do not slide)."""
    script = redis.register_script(_INCR_WITH_TTL)
    return int(await script(keys=[key], args=[max(1, ttl_seconds)]))


async def incr_within_limit(redis: Redis, key: str, *, limit: int, ttl_seconds: int) -> int | None:
    """Atomically take one unit of a ``limit``-sized budget.

    Returns the count after taking it, or ``None`` — without counting — when the
    budget is already spent. N concurrent callers can never take more than
    ``limit`` units in total.
    """
    script = redis.register_script(_INCR_WITHIN_LIMIT)
    count = int(await script(keys=[key], args=[limit, max(1, ttl_seconds)]))
    return None if count < 0 else count


async def incr_distinct_within_limit(
    redis: Redis,
    key: str,
    seen_key: str,
    member: str,
    *,
    limit: int,
    ttl_seconds: int,
) -> int | None:
    """Like :func:`incr_within_limit`, but each ``member`` is charged once.

    ``seen_key`` is a set of the members already charged against ``key``. A
    member in it costs nothing and returns the current count; a new member is
    charged and added. Returns ``None`` — without counting or adding — when the
    budget is already spent. N concurrent callers with the same new member take
    exactly one unit.
    """
    script = redis.register_script(_INCR_DISTINCT_WITHIN_LIMIT)
    count = int(await script(keys=[key, seen_key], args=[member, limit, max(1, ttl_seconds)]))
    return None if count < 0 else count


async def decr_floor_zero(redis: Redis, key: str) -> int:
    """Give back one unit taken by :func:`incr_within_limit`; never below zero."""
    script = redis.register_script(_DECR_FLOOR_ZERO)
    return int(await script(keys=[key], args=[]))
