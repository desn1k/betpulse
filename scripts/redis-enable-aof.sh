#!/usr/bin/env bash
# One-time move of an existing Redis to AOF, before the first deploy of a
# release that runs Redis with `--appendonly yes` (HANDOFF §6, "Redis:
# password, memory, persistence"). Started that way over an RDB-only volume,
# Redis 7.4 comes up EMPTY; deploy.sh refuses until this has run
# (scripts/redis-persistence.sh).
#
#   1. BGSAVE, and wait until it has finished and succeeded;
#   2. copy that dump out of the container into .release/redis/ (or
#      REDIS_BACKUP_DIR) with its sha256: the restore point;
#   3. CONFIG SET appendonly yes, and wait until the AOF rewrite has finished
#      and succeeded: the AOF now holds every key, TTLs included.
# A stopped Redis is started on its volume for this, without AOF and without a
# password (a throwaway container on the internal network only), converted and
# stopped again. Nothing to do (exit 0) when AOF is already on or there is no
# Redis data. The site stays up throughout.
#
# The running Redis keeps AOF on only until it restarts: deploy right after.
#
# Usage, on the server from the checkout: scripts/redis-enable-aof.sh
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${REDIS_BACKUP_DIR:-$root_dir/.release/redis}"
wait_seconds="${REDIS_AOF_WAIT_SECONDS:-600}"

compose() { "$root_dir/scripts/prod-compose.sh" "$@"; }
# shellcheck source=scripts/redis-persistence.sh
. "$root_dir/scripts/redis-persistence.sh"

temporary=""
stop_temporary() {
  if [[ -n "$temporary" ]]; then
    docker stop "$temporary" >/dev/null 2>&1 || true
    temporary=""
  fi
}
trap stop_temporary EXIT

answers_ping() { [[ "$(redis_cli "$container" ping 2>/dev/null)" == "PONG" ]]; }
temporary_gone() { [[ -z "$(docker ps -q --filter "name=^${temporary}\$")" ]]; }
aof_done() {
  [[ "$(redis_info_field "$container" aof_enabled)" == "1" &&
    "$(redis_info_field "$container" aof_rewrite_in_progress)" == "0" &&
    "$(redis_info_field "$container" aof_rewrite_scheduled)" == "0" ]]
}

container="$(compose ps -q redis)"
if [[ -z "$container" ]]; then
  state="$(redis_volume_state)"
  if [[ "$state" != "rdb-only" ]]; then
    echo "Redis is stopped and its volume holds no RDB-only data ($state): nothing to do."
    exit 0
  fi
  temporary="betpulse-redis-aof-$$"
  echo "Redis is stopped: starting a temporary Redis on its volume, without AOF."
  compose run -d --rm --no-deps --name "$temporary" -e REDIS_PASSWORD= redis \
    redis-server --appendonly no --loglevel warning >/dev/null
  container="$temporary"
  redis_wait_until "$wait_seconds" "the temporary Redis" answers_ping
fi

if [[ "$(redis_cli "$container" config get appendonly | tail -n 1)" == "yes" ]]; then
  echo "AOF is already on ($(redis_cli "$container" dbsize) keys): nothing to do."
  exit 0
fi
keys="$(redis_cli "$container" dbsize)"
echo "Redis holds $keys keys; AOF is off."

# 1 and 2. BGSAVE and the copy out of the container.
if ! redis_dump_copy "$container" "$backup_dir" "$wait_seconds"; then
  echo "No usable dump copy; AOF left off." >&2
  exit 1
fi

# 3. AOF on, live: Redis writes the whole dataset into a new AOF.
redis_cli "$container" config set appendonly yes >/dev/null
redis_wait_until "$wait_seconds" "the AOF rewrite" aof_done
if [[ "$(redis_info_field "$container" aof_last_bgrewrite_status)" != "ok" ||
  "$(redis_info_field "$container" aof_last_write_status)" != "ok" ]]; then
  echo "The AOF rewrite failed; the dump copy above is the restore point. See the Redis log." >&2
  exit 1
fi
keys_after="$(redis_cli "$container" dbsize)"

if [[ -n "$temporary" ]]; then
  redis_cli "$container" shutdown >/dev/null 2>&1 || true
  redis_wait_until "$wait_seconds" "the temporary Redis to stop" temporary_gone
  temporary=""
  echo "AOF is on in the volume: $keys_after keys. The temporary Redis is stopped."
else
  echo "AOF is on: $keys_after keys. It holds until Redis restarts: deploy now."
fi
