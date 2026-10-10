# shellcheck shell=bash
# Sourced by deploy.sh, redis-enable-aof.sh, redis-backup.sh and redis-restore.sh.
#
# Redis runs with AOF (infra/docker-compose.yml). Started with
# `--appendonly yes` over a volume that holds only an RDB snapshot, Redis 7.4
# creates a new, EMPTY AOF and ignores dump.rdb: the ARQ queues, quotas and
# rate-limit counters would be gone (checked on 2026-10-10; HANDOFF §9i).
#
# check_redis_persistence RERUN_COMMAND
#
# Before anything is pulled or started: returns 1 with the one-time procedure
# (scripts/redis-enable-aof.sh, then RERUN_COMMAND) while the project's Redis,
# running or stopped, holds an RDB-only dataset; 0 when AOF is already on,
# when the running Redis is empty, or when there is no Redis data yet.
#
# Needs the caller's `compose` function.

# redis_cli CONTAINER ARGS...: redis-cli inside the container, with the
# container's REDIS_PASSWORD when it has one (earlier releases ran Redis
# without). The password never leaves the container.
redis_cli() {
  local container="$1"
  shift
  # shellcheck disable=SC2016 # expanded by the container's shell
  docker exec "$container" sh -c \
    'if [ -n "${REDIS_PASSWORD:-}" ]; then export REDISCLI_AUTH="$REDIS_PASSWORD"; fi; exec redis-cli --no-auth-warning "$@"' \
    sh "$@"
}

# redis_volume_state: aof, rdb-only or empty, read from the Redis volume by a
# throwaway container of the redis service (for a Redis that is not running).
redis_volume_state() {
  compose run --rm --no-deps -T --entrypoint sh redis -c \
    'if [ -d /data/appendonlydir ]; then echo aof; elif [ -s /data/dump.rdb ]; then echo rdb-only; else echo empty; fi'
}

check_redis_persistence() {
  local rerun="$1" container appendonly keys state
  container="$(compose ps -q redis 2>/dev/null || true)"
  if [[ -n "$container" ]]; then
    if ! appendonly="$(redis_cli "$container" config get appendonly)" ||
      ! keys="$(redis_cli "$container" dbsize)"; then
      echo "Cannot read the running Redis's persistence setting (redis-cli failed); nothing was changed." >&2
      return 1
    fi
    appendonly="$(tail -n 1 <<<"$appendonly")"
    if [[ "$appendonly" == "yes" || "$keys" == "0" ]]; then
      return 0
    fi
    state="Redis runs without AOF and holds $keys keys (job queues, quotas, rate-limit counters)."
  else
    if ! state="$(redis_volume_state)"; then
      echo "Cannot inspect the Redis volume; nothing was changed." >&2
      return 1
    fi
    if [[ "$state" != "rdb-only" ]]; then
      return 0
    fi
    state="Redis is stopped, and its volume holds an RDB snapshot without AOF."
  fi
  cat >&2 <<EOF
$state
This release starts Redis with AOF on, and Redis would then start EMPTY.
Nothing was changed. One-time procedure (the site stays up):
  1. scripts/redis-enable-aof.sh   # BGSAVE, a copy of the dump in .release/redis/, then AOF on
  2. $rerun
EOF
  return 1
}

# redis_info_field CONTAINER FIELD: one field of INFO persistence.
redis_info_field() {
  redis_cli "$1" info persistence | tr -d '\r' | sed -n "s/^$2://p"
}

# redis_wait_until SECONDS DESCRIPTION CONDITION...: poll every second.
redis_wait_until() {
  local seconds="$1" what="$2" _
  shift 2
  for ((_ = 0; _ < seconds; _++)); do
    "$@" && return 0
    sleep 1
  done
  echo "Timed out after ${seconds}s waiting for $what." >&2
  return 1
}

_redis_no_bgsave() { [[ "$(redis_info_field "$1" rdb_bgsave_in_progress)" == "0" ]]; }
_redis_bgsave_done() {
  _redis_no_bgsave "$1" && [[ "$(redis_info_field "$1" rdb_saves)" -gt "$2" ]]
}

# redis_dump_copy CONTAINER BACKUP_DIR [WAIT_SECONDS]: BGSAVE, awaited and
# checked, then that dump copied out of the container into
# BACKUP_DIR/dump-<UTC>.rdb with its .sha256 beside it. Prints
# "Copied the dump to <file> (...)"; returns 1 when any step fails.
redis_dump_copy() {
  local container="$1" backup_dir="$2" seconds="${3:-600}" saves reply dir file backup
  # rdb_saves counts finished snapshots; one more than before means ours is done.
  saves="$(redis_info_field "$container" rdb_saves)"
  if ! reply="$(redis_cli "$container" bgsave)" || [[ "$reply" != *"Background saving started"* ]]; then
    # One already running: let it finish, then take ours.
    redis_wait_until "$seconds" "the running snapshot" _redis_no_bgsave "$container" || return 1
    saves="$(redis_info_field "$container" rdb_saves)"
    redis_cli "$container" bgsave >/dev/null
  fi
  redis_wait_until "$seconds" "BGSAVE" _redis_bgsave_done "$container" "$saves" || return 1
  if [[ "$(redis_info_field "$container" rdb_last_bgsave_status)" != "ok" ]]; then
    echo "BGSAVE failed (rdb_last_bgsave_status is not ok). See the Redis log." >&2
    return 1
  fi
  dir="$(redis_cli "$container" config get dir | tail -n 1)"
  file="$(redis_cli "$container" config get dbfilename | tail -n 1)"
  (umask 077 && mkdir -p "$backup_dir")
  backup="$backup_dir/dump-$(date -u +%Y%m%dT%H%M%SZ).rdb"
  docker cp "$container:$dir/$file" "$backup" >/dev/null || return 1
  chmod 600 "$backup"
  if [[ ! -s "$backup" ]]; then
    echo "The dump copy $backup is empty." >&2
    return 1
  fi
  (cd "$backup_dir" && sha256sum "$(basename "$backup")" >"$(basename "$backup").sha256")
  echo "Copied the dump to $backup ($(wc -c <"$backup") bytes; sha256 in $backup.sha256)."
}
