#!/usr/bin/env bash
# One-time move of an existing Redis to AOF, before the first deploy of a
# release that runs Redis with `--appendonly yes` (HANDOFF §9i, "Redis
# persistence"). Started that way over an RDB-only volume, Redis 7.4 comes up
# EMPTY; deploy.sh refuses until this has run (scripts/redis-persistence.sh).
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

# info_field FIELD: one field of INFO persistence.
info_field() {
  redis_cli "$container" info persistence | tr -d '\r' | sed -n "s/^$1://p"
}

# wait_until DESCRIPTION CONDITION...: poll every second, at most wait_seconds.
wait_until() {
  local what="$1" _
  shift
  for ((_ = 0; _ < wait_seconds; _++)); do
    "$@" && return 0
    sleep 1
  done
  echo "Timed out after ${wait_seconds}s waiting for $what." >&2
  return 1
}

answers_ping() { [[ "$(redis_cli "$container" ping 2>/dev/null)" == "PONG" ]]; }
no_bgsave_running() { [[ "$(info_field rdb_bgsave_in_progress)" == "0" ]]; }
temporary_gone() { [[ -z "$(docker ps -q --filter "name=^${temporary}\$")" ]]; }

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
  wait_until "the temporary Redis" answers_ping
fi

if [[ "$(redis_cli "$container" config get appendonly | tail -n 1)" == "yes" ]]; then
  echo "AOF is already on ($(redis_cli "$container" dbsize) keys): nothing to do."
  exit 0
fi
keys="$(redis_cli "$container" dbsize)"
echo "Redis holds $keys keys; AOF is off."

# 1. BGSAVE. rdb_saves counts finished snapshots; one more means ours is done.
saves="$(info_field rdb_saves)"
bgsave_done() {
  [[ "$(info_field rdb_bgsave_in_progress)" == "0" && "$(info_field rdb_saves)" -gt "$saves" ]]
}
if ! reply="$(redis_cli "$container" bgsave)" || [[ "$reply" != *"Background saving started"* ]]; then
  # One already running: let it finish, then take ours.
  wait_until "the running snapshot" no_bgsave_running
  saves="$(info_field rdb_saves)"
  redis_cli "$container" bgsave >/dev/null
fi
wait_until "BGSAVE" bgsave_done
if [[ "$(info_field rdb_last_bgsave_status)" != "ok" ]]; then
  echo "BGSAVE failed (rdb_last_bgsave_status is not ok); AOF left off. See the Redis log." >&2
  exit 1
fi

# 2. The dump, out of the container.
dir="$(redis_cli "$container" config get dir | tail -n 1)"
file="$(redis_cli "$container" config get dbfilename | tail -n 1)"
umask 077
mkdir -p "$backup_dir"
backup="$backup_dir/dump-$(date -u +%Y%m%dT%H%M%SZ).rdb"
docker cp "$container:$dir/$file" "$backup" >/dev/null
if [[ ! -s "$backup" ]]; then
  echo "The dump copy $backup is empty; AOF left off." >&2
  exit 1
fi
(cd "$backup_dir" && sha256sum "$(basename "$backup")" >"$(basename "$backup").sha256")
echo "Copied the dump to $backup ($(wc -c <"$backup") bytes; sha256 in $backup.sha256)."

# 3. AOF on, live: Redis writes the whole dataset into a new AOF.
redis_cli "$container" config set appendonly yes >/dev/null
aof_done() {
  [[ "$(info_field aof_enabled)" == "1" && "$(info_field aof_rewrite_in_progress)" == "0" &&
    "$(info_field aof_rewrite_scheduled)" == "0" ]]
}
wait_until "the AOF rewrite" aof_done
if [[ "$(info_field aof_last_bgrewrite_status)" != "ok" || "$(info_field aof_last_write_status)" != "ok" ]]; then
  echo "The AOF rewrite failed; the dump copy above is the restore point. See the Redis log." >&2
  exit 1
fi
keys_after="$(redis_cli "$container" dbsize)"

if [[ -n "$temporary" ]]; then
  redis_cli "$container" shutdown >/dev/null 2>&1 || true
  wait_until "the temporary Redis to stop" temporary_gone
  temporary=""
  echo "AOF is on in the volume: $keys_after keys. The temporary Redis is stopped."
else
  echo "AOF is on: $keys_after keys. It holds until Redis restarts: deploy now."
fi
