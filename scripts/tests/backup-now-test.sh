#!/usr/bin/env bash
# Behaviour tests for scripts/backup-now.sh with `docker` replaced by a stub
# (no daemon). The stub answers what prod-compose.sh and the Redis helpers
# run: pg_dump / pg_restore --list in postgres, tar in mlflow, redis-cli in
# redis (a BGSAVE that finishes at once) and docker cp of the dump.
#   STUB_FAIL=pg_dump:mlflow  pg_dump of that database exits 1;
#   STUB_FAIL=empty:football  pg_dump of that database prints nothing;
#   STUB_FAIL=tar             tar in mlflow exits 1.
set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

cat >"$work/bin/docker" <<'STUB'
#!/usr/bin/env bash
args=" $* "
state="$STUB_STATE"
if [[ "$args" == *" ps -q redis "* ]]; then echo "redis-id"; exit 0; fi
if [[ "$args" == *" exec -T postgres pg_dump "* ]]; then
  db="${*: -1}"
  [[ "${STUB_FAIL:-}" == "pg_dump:$db" ]] && { echo "pg_dump: error: connection failed" >&2; exit 1; }
  [[ "${STUB_FAIL:-}" == "empty:$db" ]] && exit 0
  printf 'PGDMP-%s' "$db"
  exit 0
fi
if [[ "$args" == *" exec -T postgres pg_restore --list "* ]]; then
  body="$(cat)"
  [[ "$body" == PGDMP-* ]] || { echo "pg_restore: error: input file is too short" >&2; exit 1; }
  printf '1; 0 0 TABLE DATA public fixtures football\n2; 0 0 TABLE DATA public odds football\n'
  exit 0
fi
if [[ "$args" == *" exec -T mlflow tar "* ]]; then
  [[ "${STUB_FAIL:-}" == "tar" ]] && { echo "tar: error" >&2; exit 1; }
  tmp="$(mktemp -d)"
  echo artifact >"$tmp/model.joblib"
  tar czf - -C "$tmp" .
  rm -rf "$tmp"
  exit 0
fi
if [[ "${1:-}" == "exec" && "$args" == *" redis-cli "* ]]; then
  case "$args" in
    *" info persistence "*)
      saves="$(cat "$state/saves" 2>/dev/null || echo 0)"
      printf 'rdb_saves:%s\r\nrdb_bgsave_in_progress:0\r\nrdb_last_bgsave_status:ok\r\n' "$saves" ;;
    *" bgsave "*) echo 1 >"$state/saves"; echo "Background saving started" ;;
    *" dbsize "*) echo 3 ;;
    *" config get dir "*) printf 'dir\n/data\n' ;;
    *" config get dbfilename "*) printf 'dbfilename\ndump.rdb\n' ;;
  esac
  exit 0
fi
if [[ "${1:-}" == "cp" ]]; then printf 'REDIS0011' >"${*: -1}"; exit 0; fi
exit 0
STUB
chmod +x "$work/bin/docker"

failures=0
fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}

# A server checkout with a deployed release (prod-compose.sh needs one).
setup_root() {
  local root="$work/$1"
  mkdir -p "$root/.release"
  cp -r "$repo_dir/scripts" "$root/"
  printf 'POSTGRES_USER=football\nPOSTGRES_DB=football\n' >"$root/.env"
  echo v1.0.0 >"$root/.release/last-successful-image-tag"
  {
    echo RELEASE_VERSION=v1.0.0
    for k in API WEB MLFLOW; do echo "${k}_IMAGE_DIGEST=sha256:$(printf '%064d' 1)"; done
  } >"$root/.release/v1.0.0.digests"
  mkdir -p "$root/state"
  echo "$root"
}

# run ROOT: sets $code and $out; backups land in ROOT/out.
run() {
  set +e
  out="$(PATH="$work/bin:$PATH" STUB_STATE="$1/state" BACKUP_DIR="$1/out" \
    bash "$1/scripts/backup-now.sh" 2>&1)"
  code=$?
  set -e
}

# 1. Success: four files and their checksums, verified, in one dated directory.
root="$(setup_root ok)"
run "$root"
[[ "$code" == "0" ]] || fail "ok: exit $code: $out"
dir="$(find "$root/out" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -n 1 || true)"
for f in football.dump mlflow.dump mlflow-artifacts.tgz SHA256SUMS; do
  [[ -s "$dir/$f" ]] || fail "ok: $f missing or empty"
done
[[ -n "$(find "$dir" -name 'dump-*.rdb' -size +0)" ]] || fail "ok: no Redis copy"
(cd "$dir" && sha256sum -c --quiet SHA256SUMS) || fail "ok: SHA256SUMS does not verify"
grep -q "football.dump: 2 tables with data" <<<"$out" || fail "ok: football check not reported: $out"
grep -q "Copy this directory off the server" <<<"$out" || fail "ok: no off-server reminder"

# 2. A failing pg_dump: exit 1, named, no SHA256SUMS (an incomplete set is never "done").
root="$(setup_root pgdump)"
STUB_FAIL=pg_dump:mlflow run "$root"
[[ "$code" == "1" ]] || fail "pg_dump fails: exit $code, expected 1"
grep -q "mlflow.dump: pg_dump failed" <<<"$out" || fail "pg_dump fails: not named: $out"
[[ -z "$(find "$root/out" -name SHA256SUMS 2>/dev/null)" ]] || fail "pg_dump fails: SHA256SUMS written"

# 3. An empty dump (pg_dump printed nothing): exit 1.
root="$(setup_root empty)"
STUB_FAIL=empty:football run "$root"
[[ "$code" == "1" ]] || fail "empty dump: exit $code, expected 1"
grep -q "football.dump: empty" <<<"$out" || fail "empty dump: not named: $out"

# 4. The artifacts archive fails: exit 1.
root="$(setup_root tar)"
STUB_FAIL=tar run "$root"
[[ "$code" == "1" ]] || fail "tar fails: exit $code, expected 1"
grep -q "mlflow-artifacts.tgz: failed" <<<"$out" || fail "tar fails: not named: $out"

if ((failures > 0)); then
  echo "$failures backup-now test(s) failed." >&2
  exit 1
fi
echo "OK: backup-now.sh writes and verifies the four parts, and fails on any bad one."
