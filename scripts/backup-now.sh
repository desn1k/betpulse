#!/usr/bin/env bash
# A manual backup of everything BetPulse keeps, until automated off-server
# backups (WAL-G, HANDOFF Phase 14b) exist (docs/DEPLOY_VPS.md, section 8):
#   football.dump          pg_dump -Fc of the application database
#   mlflow.dump            pg_dump -Fc of the MLflow metadata database
#   mlflow-artifacts.tgz   the MLflow artifact store (trained models)
#   dump-<UTC>.rdb(.sha256) a Redis copy (scripts/redis-backup.sh)
#   SHA256SUMS             checksums of all of the above
# in one directory per run, .release/backups/<UTC>/ (or BACKUP_DIR/<UTC>/).
# Each part is checked: a dump must be non-empty and readable by pg_restore
# --list, the archive must list. Any failure stops the run before SHA256SUMS is
# written, so a directory without SHA256SUMS is never a complete backup.
#
# The site stays up. The files hold personal data and encrypted secrets: keep
# them as private as .env, and copy the directory off the server (the same
# disk holds the volumes). Restoring Postgres is not covered here yet.
#
# Usage, on the server from the checkout: scripts/backup-now.sh
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="${BACKUP_DIR:-$root_dir/.release/backups}/$stamp"

compose() { "$root_dir/scripts/prod-compose.sh" "$@"; }

# env_value KEY DEFAULT: KEY from .env (the last assignment), or DEFAULT.
env_value() {
  local value
  value="$(sed -n "s/^$1=//p" "$root_dir/.env" | tail -n 1 | tr -d '\r')"
  echo "${value:-$2}"
}
pg_user="$(env_value POSTGRES_USER football)"
pg_db="$(env_value POSTGRES_DB football)"

die() {
  echo "$1" >&2
  echo "Backup INCOMPLETE: $out has no SHA256SUMS; do not rely on it." >&2
  exit 1
}

umask 077
mkdir -p "$out"
echo "Backing up into $out"

for db in "$pg_db" mlflow; do
  file="$out/$db.dump"
  compose exec -T postgres pg_dump -U "$pg_user" -Fc "$db" >"$file" ||
    die "$db.dump: pg_dump failed"
  [[ -s "$file" ]] || die "$db.dump: empty"
  tables="$(compose exec -T postgres pg_restore --list <"$file" | grep -c "TABLE DATA" || true)"
  [[ "$tables" -gt 0 ]] || die "$db.dump: pg_restore cannot read it"
  echo "$db.dump: $tables tables with data, $(wc -c <"$file") bytes"
done

archive="$out/mlflow-artifacts.tgz"
compose exec -T mlflow tar czf - -C /mlflow/artifacts . >"$archive" ||
  die "mlflow-artifacts.tgz: failed"
entries="$(tar tzf "$archive" | wc -l)" || die "mlflow-artifacts.tgz: not a readable archive"
echo "mlflow-artifacts.tgz: $entries entries, $(wc -c <"$archive") bytes"

REDIS_BACKUP_DIR="$out" bash "$root_dir/scripts/redis-backup.sh" || die "redis: copy failed"

(cd "$out" && sha256sum -- * >SHA256SUMS)
echo "Done: $out (checksums in SHA256SUMS)."
echo "Copy this directory off the server now, e.g. from your machine:"
echo "  scp -r <user>@<server>:$out ."
