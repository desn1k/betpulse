#!/usr/bin/env bash
# A copy of the running Redis's data, out of the container: BGSAVE (awaited
# and checked), then the dump copied to .release/redis/dump-<UTC>.rdb (or
# REDIS_BACKUP_DIR) with its .sha256 beside it. The site stays up. Restore it
# with scripts/redis-restore.sh. Copy the file off the server as well: the
# directory is on the same disk as the volume.
#
# Usage, on the server from the checkout: scripts/redis-backup.sh
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${REDIS_BACKUP_DIR:-$root_dir/.release/redis}"

compose() { "$root_dir/scripts/prod-compose.sh" "$@"; }
# shellcheck source=scripts/redis-persistence.sh
. "$root_dir/scripts/redis-persistence.sh"

container="$(compose ps -q redis)"
if [[ -z "$container" ]]; then
  echo "Redis is not running; nothing to copy (scripts/prod-compose.sh up -d redis first)." >&2
  exit 1
fi
echo "Redis holds $(redis_cli "$container" dbsize) keys."
redis_dump_copy "$container" "$backup_dir"
