#!/usr/bin/env bash
# Behaviour tests for scripts/deploy.sh and scripts/rollback.sh (the BFF
# readiness check, the release digests they deploy by) and for
# scripts/release-version.sh, with `docker` and `sleep` replaced by stubs on
# PATH. No Docker daemon is needed.
#
# The docker stub logs every call (with the IMAGE_TAG and image digests it ran
# under) and answers `compose exec -T web wget ... /api/ready` according to
# STUB_READY:
#   ok    -> success
#   fail  -> "connection refused" (backend unreachable)
#   404   -> "server returned error: HTTP/1.1 404 Not Found" (old image)
set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$(mktemp -d)"
# Release every held stub (see STUB_BLOCK_DIR) and give it a moment to see
# that, before its directory goes away.
cleanup() {
  local dir
  for dir in "$work"/root-*/block; do
    [[ -d "$dir" ]] && : >"$dir/go"
  done
  for dir in "$work"/root-*/block; do
    for ((tick = 0; tick < 30; tick++)); do
      [[ ! -f "$dir/started" || -f "$dir/done" ]] && break
      /usr/bin/sleep 0.1
    done
  done
  rm -rf "$work"
}
trap cleanup EXIT

mkdir -p "$work/bin"
cat >"$work/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "IMAGE_TAG=${IMAGE_TAG:-} API=${API_IMAGE_DIGEST:-} WEB=${WEB_IMAGE_DIGEST:-} MLFLOW=${MLFLOW_IMAGE_DIGEST:-} $*" >>"$STUB_LOG"
if [[ "${1:-}" == "inspect" ]]; then
  # STUB_UNHEALTHY_TAG: containers of that release never become healthy.
  if [[ -n "${STUB_UNHEALTHY_TAG:-}" && "${IMAGE_TAG:-}" == "$STUB_UNHEALTHY_TAG" ]]; then
    echo unhealthy
  else
    echo healthy
  fi
  exit 0
fi
args=" $* "
# STUB_BLOCK_DIR: `compose pull` writes <dir>/started, then waits for <dir>/go
# (a deploy held mid-run, to signal it) and writes <dir>/done. The wait is
# bounded (STUB_BLOCK_TICKS x 0.1 s, default 30 s): a stub whose test is gone
# fails instead of looping forever (seen once after the rc5 work).
if [[ -n "${STUB_BLOCK_DIR:-}" && "$args" == *" pull "* ]]; then
  : >"$STUB_BLOCK_DIR/started"
  for ((tick = 0; tick < ${STUB_BLOCK_TICKS:-300}; tick++)); do
    [[ -f "$STUB_BLOCK_DIR/go" ]] && break
    /usr/bin/sleep 0.1
  done
  : >"$STUB_BLOCK_DIR/done" 2>/dev/null || true
  [[ -f "$STUB_BLOCK_DIR/go" ]] || exit 1
fi
if [[ "$args" == *" ps -q "* ]]; then
  echo "container-id"
  exit 0
fi
if [[ "$args" == *" exec -T -e BP_DB="*" postgres "* ]]; then
  # psql reads its SQL from stdin: log it with its database and answer the
  # catalog queries. Only the app database (football) has the extension.
  db="${args#* BP_DB=}"
  db="${db%% *}"
  sql="$(cat)"
  echo "SQL[$db]: $sql" >>"$STUB_LOG"
  # STUB_PSQL_FAIL_DB: every psql session on that database fails.
  if [[ -n "${STUB_PSQL_FAIL_DB:-}" && "$db" == "$STUB_PSQL_FAIL_DB" ]]; then
    exit 1
  fi
  if [[ "$sql" == *"FROM pg_database"* ]]; then
    printf 'football\nmlflow\npostgres\n'
  elif [[ "$sql" == *"SELECT extversion"* && "$db" == "football" &&
    "${STUB_TIMESCALE:-absent}" == "installed" ]]; then
    echo "2.17.2"
  fi
  exit 0
fi
if [[ "$args" == *" exec -T web wget "* ]]; then
  # STUB_READY_FAIL_TAG: only that release's web cannot reach the API.
  if [[ -n "${STUB_READY_FAIL_TAG:-}" && "${IMAGE_TAG:-}" == "$STUB_READY_FAIL_TAG" ]]; then
    echo "wget: can't connect to remote host: Connection refused" >&2
    exit 1
  fi
  case "${STUB_READY:-ok}" in
    ok) exit 0 ;;
    404) echo "wget: server returned error: HTTP/1.1 404 Not Found" >&2; exit 1 ;;
    *) echo "wget: can't connect to remote host: Connection refused" >&2; exit 1 ;;
  esac
fi
exit 0
STUB
printf '#!/usr/bin/env bash\nexit 0\n' >"$work/bin/sleep"
chmod +x "$work/bin/docker" "$work/bin/sleep"

failures=0
fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}

# A digests file as release.yml writes it; every digest is the digit repeated.
hex_of() { printf "%064d" 0 | tr 0 "$1"; }
write_digests() {
  local file="$1" version="$2" hex
  hex="$(hex_of "$3")"
  printf 'RELEASE_VERSION=%s\nAPI_IMAGE_DIGEST=sha256:%s\nWEB_IMAGE_DIGEST=sha256:%s\nMLFLOW_IMAGE_DIGEST=sha256:%s\n' \
    "$version" "$hex" "$hex" "$hex" >"$file"
}
DIGEST1="sha256:$(hex_of 1)"
DIGEST2="sha256:$(hex_of 2)"

# A fresh fake repo root per scenario: deploy.sh reads .env and writes
# .release/ next to its own scripts/ directory. v1.0.0 is the last successful
# deploy (its digests stored); the release asset for v2.0.0 sits in the root.
setup_root() {
  local root="$work/root-$1"
  mkdir -p "$root/scripts" "$root/.release"
  cp "$repo_dir/scripts/deploy.sh" "$repo_dir/scripts/rollback.sh" \
    "$repo_dir/scripts/prod-compose.sh" "$repo_dir/scripts/release-digests.sh" "$root/scripts/"
  : >"$root/.env"
  echo "v1.0.0" >"$root/.release/last-successful-image-tag"
  write_digests "$root/.release/v1.0.0.digests" v1.0.0 1
  write_digests "$root/release-v2.0.0.digests" v2.0.0 2
  echo "$root"
}

# run ROOT READY SCRIPT TAG [DIGESTS_FILE]   (no DIGESTS_FILE: RELEASE_DIGESTS unset)
run() {
  local root="$1" ready="$2" script="$3" tag="$4" digests="${5:-}"
  local log="$root/docker.log"
  : >"$log"
  set +e
  PATH="$work/bin:$PATH" STUB_LOG="$log" STUB_READY="$ready" IMAGE_TAG="$tag" \
    STUB_READY_FAIL_TAG="${READY_FAIL_TAG:-}" STUB_UNHEALTHY_TAG="${UNHEALTHY_TAG:-}" \
    STUB_TIMESCALE="${TIMESCALE:-absent}" STUB_PSQL_FAIL_DB="${PSQL_FAIL_DB:-}" \
    BETPULSE_RELEASE_LOCK_PID="${LOCK_PID:-}" \
    RELEASE_DIGESTS="$digests" DEPLOY_HEALTHCHECK_ATTEMPTS=2 \
    bash "$root/scripts/$script" >"$root/out.txt" 2>&1
  local code=$?
  set -e
  echo "$code"
}

app="api worker-realtime worker-batch worker-ml web mlflow"

# Every file in ROOT/.release, hidden ones included, sorted, space-separated.
state_files() {
  find "$1/.release" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort | tr '\n' ' '
}

# The deploy/rollback lock must be gone once a run has ended.
lock_released() {
  [[ ! -e "$1/.release/.deploy.lock" ]] || fail "$2: lock not released"
}

# take_lock ROOT PID: the lock as a run with that PID holds it.
take_lock() {
  mkdir -p "$1/.release/.deploy.lock"
  echo "$2" >"$1/.release/.deploy.lock/pid"
}

# 1. deploy, backend reachable: success, new tag and its digests recorded,
# every app image (mlflow included) pulled by the release digests.
root="$(setup_root ok)"
code="$(run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "0" ]] || fail "deploy ok: exit $code, expected 0"
[[ "$(<"$root/.release/last-successful-image-tag")" == "v2.0.0" ]] || fail "deploy ok: tag not recorded"
grep -q "exec -T web wget -q -T 5 -O /dev/null http://127.0.0.1:3000/api/ready" "$root/docker.log" ||
  fail "deploy ok: readiness probe not run inside the web container"
grep -q "^IMAGE_TAG=v2.0.0 API=$DIGEST2 WEB=$DIGEST2 MLFLOW=$DIGEST2 .* pull $app\$" "$root/docker.log" ||
  fail "deploy ok: app images (mlflow included) not pulled by the release digests"
cmp -s "$root/release-v2.0.0.digests" "$root/.release/v2.0.0.digests" ||
  fail "deploy ok: digests of the deployed release not stored"

# 1b. first launch: no TimescaleDB extension yet, so no ALTER EXTENSION, but
# every database is checked.
for db in football mlflow postgres; do
  grep -q "SQL\[$db\]: SELECT extversion" "$root/docker.log" ||
    fail "deploy ok: extension not checked in $db"
done
if grep -q "ALTER EXTENSION" "$root/docker.log"; then
  fail "deploy ok: ALTER EXTENSION run on a database without the extension"
fi

# 1c. existing database: the extension is updated, only where it is installed
# (the app database), and before the migrations run.
root="$(setup_root timescale)"
log="$root/docker.log"
: >"$log"
set +e
PATH="$work/bin:$PATH" STUB_LOG="$log" STUB_READY=ok STUB_TIMESCALE=installed IMAGE_TAG=v2.0.0 \
  RELEASE_DIGESTS="$root/release-v2.0.0.digests" DEPLOY_HEALTHCHECK_ATTEMPTS=2 \
  bash "$root/scripts/deploy.sh" >"$root/out.txt" 2>&1
code=$?
set -e
[[ "$code" == "0" ]] || fail "deploy timescale: exit $code, expected 0"
alter_line="$(grep -n "SQL\[football\]: ALTER EXTENSION timescaledb UPDATE" "$log" | head -1 | cut -d: -f1)"
alembic_line="$(grep -n "run --rm api alembic upgrade head" "$log" | head -1 | cut -d: -f1)"
[[ -n "$alter_line" && -n "$alembic_line" && "$alter_line" -lt "$alembic_line" ]] ||
  fail "deploy timescale: ALTER EXTENSION must run in football before the migrations"
if grep -q "SQL\[mlflow\]: ALTER EXTENSION" "$log"; then
  fail "deploy timescale: ALTER EXTENSION run in a database without the extension"
fi

# 2. deploy fails /api/ready, the previous release is fine: rolled back through
# rollback.sh by the stored digests, which waits for health and /api/ready
# under the previous release; exit 2. The failed release is not recorded, and
# the schema warning is printed (migrations already ran).
root="$(setup_root unreachable)"
code="$(READY_FAIL_TAG=v2.0.0 run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "2" ]] || fail "deploy rolled back: exit $code, expected 2"
grep -q "cannot reach the API" "$root/out.txt" || fail "deploy rolled back: no clear message"
grep -q "Deployment of v2.0.0 failed; rolled back to v1.0.0, which is healthy and ready" "$root/out.txt" ||
  fail "deploy rolled back: outcome not reported"
grep -q "^IMAGE_TAG=v1.0.0 API=$DIGEST1 WEB=$DIGEST1 MLFLOW=$DIGEST1 .* up -d --no-deps" "$root/docker.log" ||
  fail "deploy rolled back: previous images not restarted by their stored digests"
grep -q "^IMAGE_TAG=v1.0.0 API=$DIGEST1 .* exec -T web wget .*/api/ready" "$root/docker.log" ||
  fail "deploy rolled back: /api/ready not checked under the previous release"
grep -q "^IMAGE_TAG=v1.0.0 .* inspect " "$root/docker.log" ||
  fail "deploy rolled back: health not waited for under the previous release"
grep -q "database schema is NOT rolled back" "$root/out.txt" ||
  fail "deploy rolled back: no schema warning after migrations ran"
[[ "$(<"$root/.release/last-successful-image-tag")" == "v1.0.0" ]] ||
  fail "deploy rolled back: last successful tag overwritten"
[[ ! -f "$root/.release/v2.0.0.digests" ]] || fail "deploy rolled back: failed release stored"
[[ "$(state_files "$root")" == "last-successful-image-tag v1.0.0.digests " ]] ||
  fail "deploy rolled back: .release/ holds more than the previous release's state"
write_digests "$root/expected-v1.digests" v1.0.0 1
cmp -s "$root/expected-v1.digests" "$root/.release/v1.0.0.digests" ||
  fail "deploy rolled back: stored digests of the previous release changed"
lock_released "$root" "deploy rolled back (exit 2)"

# 2b. the previous release cannot reach the API either: the rollback fails, exit 3.
root="$(setup_root rollback-fails)"
code="$(run "$root" fail deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "3" ]] || fail "deploy rollback failed: exit $code, expected 3"
grep -q "AUTOMATIC ROLLBACK TO v1.0.0 FAILED" "$root/out.txt" ||
  fail "deploy rollback failed: outcome not reported"
grep -q "database schema is NOT rolled back" "$root/out.txt" ||
  fail "deploy rollback failed: no schema warning after migrations ran"
lock_released "$root" "deploy rollback failed (exit 3)"

# 2c. the previous release never becomes healthy: the rollback fails, exit 3.
root="$(setup_root rollback-unhealthy)"
code="$(READY_FAIL_TAG=v2.0.0 UNHEALTHY_TAG=v1.0.0 run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "3" ]] || fail "deploy rollback unhealthy: exit $code, expected 3"
grep -q "AUTOMATIC ROLLBACK TO v1.0.0 FAILED" "$root/out.txt" ||
  fail "deploy rollback unhealthy: outcome not reported"

# 2d. the new release fails before the migrations (Postgres never healthy):
# rolled back, exit 2, and the message says no migration ran.
root="$(setup_root before-migrations)"
code="$(UNHEALTHY_TAG=v2.0.0 run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "2" ]] || fail "deploy before migrations: exit $code, expected 2"
if grep -q "alembic upgrade head" "$root/docker.log"; then
  fail "deploy before migrations: migrations ran"
fi
grep -q "no migration ran, so the database schema is unchanged" "$root/out.txt" ||
  fail "deploy before migrations: schema state not reported"

# 2e. the TimescaleDB extension is updated in one database, then a later
# database check fails before the migrations: rolled back, and the message does
# not claim the schema is unchanged.
root="$(setup_root extension-updated)"
code="$(TIMESCALE=installed PSQL_FAIL_DB=mlflow run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "2" ]] || fail "deploy extension updated: exit $code, expected 2"
grep -q "SQL\[football\]: ALTER EXTENSION timescaledb UPDATE" "$root/docker.log" ||
  fail "deploy extension updated: the extension was not updated first"
grep -q "TimescaleDB extension update may have changed the database schema" "$root/out.txt" ||
  fail "deploy extension updated: the extension update not reported"
if grep -q "schema is unchanged" "$root/out.txt"; then
  fail "deploy extension updated: claimed the schema is unchanged"
fi

# 3. deploy of an image without /api/ready: fails fast with its own message; the
# previous image (the stub answers 404 for every release) has none either, so
# its readiness cannot be verified: the rollback counts as failed, exit 3.
root="$(setup_root old-image)"
code="$(run "$root" 404 deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "3" ]] || fail "deploy 404: exit $code, expected 3 (rollback not verified)"
if grep -q "healthy and ready" "$root/out.txt"; then
  fail "deploy 404: reported ready without a readiness check"
fi
grep -q "has no /api/ready" "$root/out.txt" || fail "deploy 404: no clear message"
[[ "$(grep -c "^IMAGE_TAG=v2.0.0 .* exec -T web wget" "$root/docker.log")" == "1" ]] || fail "deploy 404: probe of the new release retried"

# 4. rollback to an image without /api/ready: its readiness cannot be verified,
# so the rollback fails (every release with a digests file has /api/ready).
root="$(setup_root rollback-old)"
code="$(run "$root" 404 rollback.sh v1.0.0)"
[[ "$code" == "1" ]] || fail "rollback 404: exit $code, expected 1"
grep -q "has no /api/ready, so its readiness cannot be verified" "$root/out.txt" ||
  fail "rollback 404: no clear message"

# 5. rollback, backend unreachable: fails with a clear message.
root="$(setup_root rollback-unreachable)"
code="$(run "$root" fail rollback.sh v1.0.0)"
[[ "$code" != "0" ]] || fail "rollback unreachable: exit 0, expected failure"
grep -q "still cannot reach the API" "$root/out.txt" || fail "rollback unreachable: no clear message"

# 6. rollback, backend reachable: success, by the stored digests (mlflow included).
root="$(setup_root rollback-ok)"
code="$(run "$root" ok rollback.sh v1.0.0)"
[[ "$code" == "0" ]] || fail "rollback ok: exit $code, expected 0"
grep -q "^IMAGE_TAG=v1.0.0 API=$DIGEST1 WEB=$DIGEST1 MLFLOW=$DIGEST1 .* up -d --no-deps --remove-orphans $app\$" "$root/docker.log" ||
  fail "rollback ok: not restarted by the stored digests"

# 7. deploy without a digests file: refused before any docker call.
root="$(setup_root no-digests)"
code="$(run "$root" ok deploy.sh v2.0.0)"
[[ "$code" == "1" ]] || fail "deploy no digests: exit $code, expected 1 (refused)"
grep -q "release-v2.0.0.digests" "$root/out.txt" || fail "deploy no digests: no clear message"
[[ ! -s "$root/docker.log" ]] || fail "deploy no digests: docker was called"

# 8. digests file of another release: refused.
root="$(setup_root wrong-version)"
code="$(run "$root" ok deploy.sh v2.0.1 "$root/release-v2.0.0.digests")"
[[ "$code" == "1" ]] || fail "deploy wrong version: exit $code, expected 1 (refused)"
grep -qF "is for release 'v2.0.0', not 'v2.0.1'" "$root/out.txt" || fail "deploy wrong version: no clear message"
[[ ! -s "$root/docker.log" ]] || fail "deploy wrong version: docker was called"

# 9. malformed digest, unknown key: refused.
root="$(setup_root bad-digest)"
printf 'RELEASE_VERSION=v2.0.0\nAPI_IMAGE_DIGEST=sha256:abc\nWEB_IMAGE_DIGEST=%s\nMLFLOW_IMAGE_DIGEST=%s\n' \
  "$DIGEST2" "$DIGEST2" >"$root/bad.digests"
code="$(run "$root" ok deploy.sh v2.0.0 "$root/bad.digests")"
[[ "$code" == "1" ]] || fail "deploy bad digest: exit $code, expected 1 (refused)"
grep -qF "sha256:<64 hex>" "$root/out.txt" || fail "deploy bad digest: no clear message"
{
  cat "$root/release-v2.0.0.digests"
  echo "IMAGE_TAG=latest"
} >"$root/extra.digests"
code="$(run "$root" ok deploy.sh v2.0.0 "$root/extra.digests")"
[[ "$code" == "1" ]] || fail "deploy unknown key: exit $code, expected 1 (refused)"
[[ ! -s "$root/docker.log" ]] || fail "deploy bad digests: docker was called"

# 10. failed deploy whose previous release has no stored digests: no blind
# rollback by tag.
root="$(setup_root no-previous-digests)"
rm "$root/.release/v1.0.0.digests"
code="$(run "$root" fail deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "4" ]] || fail "deploy no previous digests: exit $code, expected 4 (no automatic rollback)"
grep -q "No stored digests for v1.0.0" "$root/out.txt" || fail "deploy no previous digests: no clear message"
if grep -q "^IMAGE_TAG=v1.0.0 " "$root/docker.log"; then
  fail "deploy no previous digests: rolled back without digests"
fi
lock_released "$root" "deploy no previous digests (exit 4)"

# 10b. first deploy on this server fails: nothing to roll back to, exit 4.
root="$(setup_root first-deploy)"
rm "$root/.release/last-successful-image-tag" "$root/.release/v1.0.0.digests"
code="$(run "$root" fail deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "4" ]] || fail "first deploy failed: exit $code, expected 4"
grep -q "no earlier release to roll back to" "$root/out.txt" || fail "first deploy failed: no clear message"

# 11. rollback to a release never deployed here: needs its digests file.
root="$(setup_root rollback-no-digests)"
code="$(run "$root" ok rollback.sh v0.9.0)"
[[ "$code" != "0" ]] || fail "rollback no digests: exit 0, expected failure"
grep -q "release-v0.9.0.digests" "$root/out.txt" || fail "rollback no digests: no clear message"
[[ ! -s "$root/docker.log" ]] || fail "rollback no digests: docker was called"

# 12. prod-compose.sh: plain compose commands get the deployed release's digests.
root="$(setup_root prod-compose)"
: >"$root/docker.log"
PATH="$work/bin:$PATH" STUB_LOG="$root/docker.log" bash "$root/scripts/prod-compose.sh" ps >/dev/null 2>&1 ||
  fail "prod-compose: exit non-zero"
grep -q "^IMAGE_TAG=v1.0.0 API=$DIGEST1 WEB=$DIGEST1 MLFLOW=$DIGEST1 compose .* ps\$" "$root/docker.log" ||
  fail "prod-compose: not run with the deployed release's tag and digests"

# 13. prod-compose.sh with nothing deployed: refused before any docker call.
root="$(setup_root prod-compose-empty)"
rm "$root/.release/last-successful-image-tag"
: >"$root/docker.log"
set +e
PATH="$work/bin:$PATH" STUB_LOG="$root/docker.log" bash "$root/scripts/prod-compose.sh" ps >"$root/out.txt" 2>&1
code=$?
set -e
[[ "$code" != "0" ]] || fail "prod-compose empty: exit 0, expected failure"
grep -q "No release deployed here yet" "$root/out.txt" || fail "prod-compose empty: no clear message"
[[ ! -s "$root/docker.log" ]] || fail "prod-compose empty: docker was called"

# 14. release-version.sh: only vX.Y.Z is stable (and gets `latest`).
check_version() {
  local version="$1" expected="$2" out code
  set +e
  out="$(bash "$repo_dir/scripts/release-version.sh" "$version" 2>/dev/null)"
  code=$?
  set -e
  if [[ "$expected" == "invalid" ]]; then
    [[ "$code" != "0" ]] || fail "release-version '$version': accepted, expected invalid"
  else
    [[ "$code" == "0" && "$out" == "stable=$expected" ]] ||
      fail "release-version '$version': got '$out' (exit $code), expected stable=$expected"
  fi
}
check_version v1.2.3 true
check_version v10.0.12 true
check_version v1.2.3-rc1 false
check_version v0.0.1-rc4 false
check_version v1.2.3.beta-2 false
check_version 1.2.3 invalid
check_version v1.2 invalid
check_version "v1.2.3 latest" invalid
check_version "" invalid

# 15. a manual rollback records the restored release as the deployed one:
# deploy v2.0.0, roll back to v1.0.0 by hand; .release/ then names v1.0.0 (its
# digests unchanged, no temporary file left), and prod-compose.sh runs it.
root="$(setup_root manual-rollback)"
code="$(run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "0" ]] || fail "manual rollback: deploy v2.0.0 exit $code, expected 0"
code="$(run "$root" ok rollback.sh v1.0.0)"
[[ "$code" == "0" ]] || fail "manual rollback: exit $code, expected 0"
[[ "$(<"$root/.release/last-successful-image-tag")" == "v1.0.0" ]] ||
  fail "manual rollback: last successful tag still names the release rolled back from"
write_digests "$root/expected-v1.digests" v1.0.0 1
cmp -s "$root/expected-v1.digests" "$root/.release/v1.0.0.digests" ||
  fail "manual rollback: stored digests of the restored release changed"
[[ "$(state_files "$root")" == "last-successful-image-tag v1.0.0.digests v2.0.0.digests " ]] ||
  fail "manual rollback: unexpected files in .release/ (temporary file left?)"
: >"$root/docker.log"
PATH="$work/bin:$PATH" STUB_LOG="$root/docker.log" bash "$root/scripts/prod-compose.sh" ps >/dev/null 2>&1 ||
  fail "manual rollback: prod-compose exit non-zero"
grep -q "^IMAGE_TAG=v1.0.0 API=$DIGEST1 WEB=$DIGEST1 MLFLOW=$DIGEST1 compose .* ps\$" "$root/docker.log" ||
  fail "manual rollback: prod-compose does not run the restored release"

# 15b. after that manual rollback, a failed deploy of v3.0.0 rolls back
# automatically to v1.0.0 (what runs), never to the rejected v2.0.0.
write_digests "$root/release-v3.0.0.digests" v3.0.0 3
code="$(READY_FAIL_TAG=v3.0.0 run "$root" ok deploy.sh v3.0.0 "$root/release-v3.0.0.digests")"
[[ "$code" == "2" ]] || fail "deploy after manual rollback: exit $code, expected 2"
grep -q "rolled back to v1.0.0, which is healthy and ready" "$root/out.txt" ||
  fail "deploy after manual rollback: not rolled back to v1.0.0"
grep -q "^IMAGE_TAG=v1.0.0 API=$DIGEST1 WEB=$DIGEST1 MLFLOW=$DIGEST1 .* up -d --no-deps" "$root/docker.log" ||
  fail "deploy after manual rollback: v1.0.0 not restarted by its digests"
if grep -q "^IMAGE_TAG=v2.0.0 " "$root/docker.log"; then
  fail "deploy after manual rollback: the rejected v2.0.0 was started again"
fi

# 15c. a failed manual rollback changes nothing in .release/.
root="$(setup_root manual-rollback-fails)"
code="$(run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "0" ]] || fail "failed manual rollback: deploy v2.0.0 exit $code, expected 0"
code="$(READY_FAIL_TAG=v1.0.0 run "$root" ok rollback.sh v1.0.0)"
[[ "$code" == "1" ]] || fail "failed manual rollback: exit $code, expected 1"
[[ "$(<"$root/.release/last-successful-image-tag")" == "v2.0.0" ]] ||
  fail "failed manual rollback: last successful tag changed"
lock_released "$root" "failed manual rollback"

# 15d. a manual rollback to a release never deployed here (RELEASE_DIGESTS
# given) stores its digests, so a later automatic rollback can use them.
root="$(setup_root manual-rollback-new)"
write_digests "$root/release-v0.9.0.digests" v0.9.0 9
code="$(run "$root" ok rollback.sh v0.9.0 "$root/release-v0.9.0.digests")"
[[ "$code" == "0" ]] || fail "manual rollback new release: exit $code, expected 0"
cmp -s "$root/release-v0.9.0.digests" "$root/.release/v0.9.0.digests" ||
  fail "manual rollback new release: digests not stored"
[[ "$(<"$root/.release/last-successful-image-tag")" == "v0.9.0" ]] ||
  fail "manual rollback new release: tag not recorded"

# 15e. deploy v2.0.0, roll back to v1.0.0 by hand, then redeploy v2.0.0 and it
# fails readiness: rolled back automatically to v1.0.0 (exit 2), not "no
# earlier release" (exit 4).
root="$(setup_root redeploy-after-rollback)"
code="$(run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "0" ]] || fail "redeploy after rollback: deploy v2.0.0 exit $code, expected 0"
code="$(run "$root" ok rollback.sh v1.0.0)"
[[ "$code" == "0" ]] || fail "redeploy after rollback: rollback exit $code, expected 0"
code="$(READY_FAIL_TAG=v2.0.0 run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "2" ]] || fail "redeploy after rollback: exit $code, expected 2"
grep -q "rolled back to v1.0.0, which is healthy and ready" "$root/out.txt" ||
  fail "redeploy after rollback: not rolled back to v1.0.0"
[[ "$(<"$root/.release/last-successful-image-tag")" == "v1.0.0" ]] ||
  fail "redeploy after rollback: last successful tag is not v1.0.0"

# 16. deploy and rollback hold one lock (.release/.deploy.lock). Held by a
# running process: both refuse with exit 1 before any docker call, and leave
# the other run's lock in place.
root="$(setup_root lock-held)"
take_lock "$root" "$$"
for script in deploy.sh rollback.sh; do
  if [[ "$script" == deploy.sh ]]; then
    code="$(run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
  else
    code="$(run "$root" ok rollback.sh v1.0.0)"
  fi
  [[ "$code" == "1" ]] || fail "lock held, $script: exit $code, expected 1"
  grep -q "another deploy or rollback is running (pid $$)" "$root/out.txt" ||
    fail "lock held, $script: no clear message"
  [[ ! -s "$root/docker.log" ]] || fail "lock held, $script: docker was called"
  [[ "$(<"$root/.release/.deploy.lock/pid")" == "$$" ]] ||
    fail "lock held, $script: the other run's lock was removed or changed"
done

# 16b. a stale lock (its process is gone): exit 1, says it is stale and prints
# the exact command to remove it; never removed automatically.
root="$(setup_root lock-stale)"
bash -c 'exit 0' &
dead_pid=$!
wait "$dead_pid"
take_lock "$root" "$dead_pid"
code="$(run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "1" ]] || fail "stale lock: exit $code, expected 1"
grep -q "stale" "$root/out.txt" || fail "stale lock: not called stale"
grep -qF "rm -rf '$root/.release/.deploy.lock'" "$root/out.txt" ||
  fail "stale lock: no exact command to remove it"
[[ ! -s "$root/docker.log" ]] || fail "stale lock: docker was called"
[[ -d "$root/.release/.deploy.lock" ]] || fail "stale lock: removed automatically"

# 16c. the inherited-lock variable without the lock behind it: a manual
# rollback refuses instead of running unlocked.
root="$(setup_root lock-inherited-none)"
code="$(LOCK_PID=$$ run "$root" ok rollback.sh v1.0.0)"
[[ "$code" == "1" ]] || fail "inherited lock, none held: exit $code, expected 1"
grep -q "BETPULSE_RELEASE_LOCK_PID" "$root/out.txt" ||
  fail "inherited lock, none held: no clear message"
[[ ! -s "$root/docker.log" ]] || fail "inherited lock, none held: docker was called"

# 16d. the variable names a held lock, but not one held by the caller's parent
# deploy.sh: refused as well, and the lock is left alone.
root="$(setup_root lock-inherited-foreign)"
take_lock "$root" "$$"
code="$(LOCK_PID=$$ run "$root" ok rollback.sh v1.0.0)"
[[ "$code" == "1" ]] || fail "inherited lock, foreign: exit $code, expected 1"
[[ ! -s "$root/docker.log" ]] || fail "inherited lock, foreign: docker was called"
[[ -d "$root/.release/.deploy.lock" ]] || fail "inherited lock, foreign: lock removed"

# 16e. first deploy on an empty server: no .release/ yet; it is created, the
# lock taken in it, and only the release state left behind.
root="$(setup_root lock-empty-server)"
rm -rf "$root/.release"
code="$(run "$root" ok deploy.sh v2.0.0 "$root/release-v2.0.0.digests")"
[[ "$code" == "0" ]] || fail "empty server: exit $code, expected 0"
[[ "$(state_files "$root")" == "last-successful-image-tag v2.0.0.digests " ]] ||
  fail "empty server: unexpected .release/ contents"

# 16f. SIGTERM during a deploy (held at the image pull): the run exits 143 and
# releases its lock.
root="$(setup_root lock-sigterm)"
mkdir -p "$root/block"
: >"$root/docker.log"
PATH="$work/bin:$PATH" STUB_LOG="$root/docker.log" STUB_READY=ok STUB_BLOCK_DIR="$root/block" \
  IMAGE_TAG=v2.0.0 RELEASE_DIGESTS="$root/release-v2.0.0.digests" DEPLOY_HEALTHCHECK_ATTEMPTS=2 \
  bash "$root/scripts/deploy.sh" >"$root/out.txt" 2>&1 &
deploy_pid=$!
for ((i = 0; i < 300; i++)); do
  [[ -f "$root/block/started" ]] && break
  /usr/bin/sleep 0.1
done
[[ -d "$root/.release/.deploy.lock" ]] || fail "sigterm: lock not held during the deploy"
kill -TERM "$deploy_pid"
: >"$root/block/go"
set +e
wait "$deploy_pid"
code=$?
set -e
[[ "$code" == "143" ]] || fail "sigterm: exit $code, expected 143"
for ((i = 0; i < 300; i++)); do
  [[ -f "$root/block/done" ]] && break
  /usr/bin/sleep 0.1
done
[[ -f "$root/block/done" ]] || fail "sigterm: the held docker stub never finished"
lock_released "$root" "sigterm"
[[ "$(<"$root/.release/last-successful-image-tag")" == "v1.0.0" ]] ||
  fail "sigterm: release state changed"

# 16g. the lock's traps are chained after existing ones, never replace them
# (an EXIT trap with a quote in it still runs; the ERR trap is untouched).
root="$(setup_root lock-trap-chain)"
out="$(bash -c '
  . "$1/scripts/release-digests.sh"
  trap "echo old-exit '\''quoted'\''" EXIT
  trap "echo err" ERR
  acquire_release_lock "$1/.release" || exit 9
  trap -p ERR
' _ "$root" 2>&1)"
[[ "$out" == *"trap -- 'echo err' ERR"* ]] || fail "trap chain: ERR trap changed"
[[ "$out" == *"old-exit quoted"* ]] || fail "trap chain: existing EXIT trap replaced"
lock_released "$root" "trap chain"

# 16h. the PID cannot be written (disk full): the new lock is removed again and
# the run refused; no ownership, no traps left behind.
root="$(setup_root lock-pid-write-fails)"
set +e
out="$(bash -c '
  set -Eeuo pipefail
  . "$1/scripts/release-digests.sh"
  printf() { return 1; }
  acquire_release_lock "$1/.release" && exit 0
  echo "refused owned=$release_lock_owned exit-trap=[$(trap -p EXIT)]"
  exit 1
' _ "$root" 2>&1)"
code=$?
set -e
[[ "$code" == "1" ]] || fail "lock pid write fails: exit $code, expected 1"
[[ "$out" == *"refused owned=false exit-trap=[]"* ]] ||
  fail "lock pid write fails: ownership or traps left behind ($out)"
lock_released "$root" "lock pid write fails"

# 16i. the held docker stub itself: with no go file it gives up after its
# deadline (fails, writes done) rather than waiting forever.
root="$(setup_root stub-deadline)"
mkdir -p "$root/block"
set +e
STUB_LOG="$root/docker.log" STUB_BLOCK_DIR="$root/block" STUB_BLOCK_TICKS=3 \
  "$work/bin/docker" compose pull api >/dev/null 2>&1
code=$?
set -e
[[ "$code" == "1" ]] || fail "stub deadline: exit $code, expected 1"
[[ -f "$root/block/done" ]] || fail "stub deadline: done marker not written"

if ((failures > 0)); then
  echo "$failures deploy-script test(s) failed." >&2
  exit 1
fi
echo "OK: deploy, rollback, prod-compose and release-version checks behave as expected (36 scenarios)."
