#!/usr/bin/env bash
# Put a copy made by scripts/redis-backup.sh (or redis-enable-aof.sh) back as
# Redis's data, replacing what Redis holds now. Every key written after the
# copy is lost; keys keep the TTLs they had in the copy.
#
#   1. checks the copy against its .sha256;
#   2. if Redis is running, takes a copy of the current data first (the way
#      back from this restore);
#   3. stops redis, puts the copy into its volume as dump.rdb and removes the
#      AOF (Redis would load the AOF and ignore the copy);
#   4. converts the copy to AOF (scripts/redis-enable-aof.sh, stopped-Redis
#      path) and starts redis.
# Redis is down from step 3 until it answers again: the API and workers fail
# their Redis calls for that time (seconds for a small dataset).
#
# Usage, on the server from the checkout:
#   scripts/redis-restore.sh .release/redis/dump-<UTC>.rdb --replace-current-data
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${REDIS_BACKUP_DIR:-$root_dir/.release/redis}"

compose() { "$root_dir/scripts/prod-compose.sh" "$@"; }
# shellcheck source=scripts/redis-persistence.sh
. "$root_dir/scripts/redis-persistence.sh"

copy="${1:-}"
confirm="${2:-}"
if [[ -z "$copy" || ! -s "$copy" ]]; then
  echo "Usage: scripts/redis-restore.sh <dump-<UTC>.rdb> --replace-current-data" >&2
  exit 1
fi
if [[ ! -f "$copy.sha256" ]] || ! (cd "$(dirname "$copy")" && sha256sum -c --status "$(basename "$copy").sha256"); then
  echo "$copy does not match $copy.sha256 (or that file is missing): not restoring it." >&2
  exit 1
fi
container="$(compose ps -q redis)"
current="Redis is not running."
if [[ -n "$container" ]]; then
  current="Redis holds $(redis_cli "$container" dbsize) keys now."
fi
if [[ "$confirm" != "--replace-current-data" ]]; then
  cat >&2 <<EOF
$current
This replaces them with $copy: every key written after that copy is lost.
Nothing was changed. To go ahead, run again with --replace-current-data.
EOF
  exit 1
fi

if [[ -n "$container" ]]; then
  echo "$current Copying them first (the way back from this restore):"
  redis_dump_copy "$container" "$backup_dir"
fi

echo "Stopping redis and putting the copy into its volume."
compose stop redis >/dev/null
# The container stays (stopped); create it if it is gone.
target="$(compose ps -aq redis)"
if [[ -z "$target" ]]; then
  compose up --no-start --no-deps redis >/dev/null
  target="$(compose ps -aq redis)"
fi
docker cp "$copy" "$target:/data/dump.rdb"
compose run --rm --no-deps -T --entrypoint sh redis -c \
  'rm -rf /data/appendonlydir && chown redis:redis /data/dump.rdb && chmod 600 /data/dump.rdb'

bash "$root_dir/scripts/redis-enable-aof.sh"

compose up -d --no-deps redis >/dev/null
container="$(compose ps -q redis)"
answers_ping() { [[ "$(redis_cli "$container" ping 2>/dev/null)" == "PONG" ]]; }
redis_wait_until 120 "redis to answer" answers_ping
echo "Restored: Redis holds $(redis_cli "$container" dbsize) keys, AOF $(redis_cli "$container" config get appendonly | tail -n 1)."
