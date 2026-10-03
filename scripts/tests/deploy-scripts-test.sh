#!/usr/bin/env bash
# Behaviour tests for scripts/deploy.sh and scripts/rollback.sh around the
# BFF readiness check (GET /api/ready inside the web container), with `docker`
# and `sleep` replaced by stubs on PATH. No Docker daemon is needed.
#
# The docker stub logs every call (with the IMAGE_TAG it ran under) and answers
# `compose exec -T web wget ... /api/ready` according to STUB_READY:
#   ok    -> success
#   fail  -> "connection refused" (backend unreachable)
#   404   -> "server returned error: HTTP/1.1 404 Not Found" (old image)
set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/bin"
cat >"$work/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "IMAGE_TAG=${IMAGE_TAG:-} $*" >>"$STUB_LOG"
if [[ "${1:-}" == "inspect" ]]; then
  echo healthy
  exit 0
fi
args=" $* "
if [[ "$args" == *" ps -q "* ]]; then
  echo "container-id"
  exit 0
fi
if [[ "$args" == *" exec -T web wget "* ]]; then
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

# A fresh fake repo root per scenario: deploy.sh reads .env and writes
# .release/ next to its own scripts/ directory.
setup_root() {
  local root="$work/root-$1"
  mkdir -p "$root/scripts" "$root/.release"
  cp "$repo_dir/scripts/deploy.sh" "$repo_dir/scripts/rollback.sh" "$root/scripts/"
  : >"$root/.env"
  echo "v1.0.0" >"$root/.release/last-successful-image-tag"
  echo "$root"
}

run() {
  local root="$1" ready="$2" script="$3" tag="$4"
  local log="$root/docker.log"
  : >"$log"
  set +e
  PATH="$work/bin:$PATH" STUB_LOG="$log" STUB_READY="$ready" IMAGE_TAG="$tag" \
    DEPLOY_HEALTHCHECK_ATTEMPTS=2 bash "$root/scripts/$script" >"$root/out.txt" 2>&1
  local code=$?
  set -e
  echo "$code"
}

# 1. deploy, backend reachable: success, new tag recorded.
root="$(setup_root ok)"
code="$(run "$root" ok deploy.sh v2.0.0)"
[[ "$code" == "0" ]] || fail "deploy ok: exit $code, expected 0"
[[ "$(<"$root/.release/last-successful-image-tag")" == "v2.0.0" ]] || fail "deploy ok: tag not recorded"
grep -q "exec -T web wget -q -O /dev/null http://localhost:3000/api/ready" "$root/docker.log" ||
  fail "deploy ok: readiness probe not run inside the web container"

# 2. deploy, backend unreachable: fails, rolls back to the previous tag.
root="$(setup_root unreachable)"
code="$(run "$root" fail deploy.sh v2.0.0)"
[[ "$code" != "0" ]] || fail "deploy unreachable: exit 0, expected failure"
grep -q "cannot reach the API" "$root/out.txt" || fail "deploy unreachable: no clear message"
grep -q "Deployment failed; restoring application images tagged v1.0.0" "$root/out.txt" ||
  fail "deploy unreachable: rollback not announced"
grep -q "^IMAGE_TAG=v1.0.0 .* up -d --no-deps" "$root/docker.log" ||
  fail "deploy unreachable: previous images not restarted"
[[ "$(<"$root/.release/last-successful-image-tag")" == "v1.0.0" ]] ||
  fail "deploy unreachable: last successful tag overwritten"

# 3. deploy of an image without /api/ready: fails fast with its own message.
root="$(setup_root old-image)"
code="$(run "$root" 404 deploy.sh v2.0.0)"
[[ "$code" != "0" ]] || fail "deploy 404: exit 0, expected failure"
grep -q "has no /api/ready" "$root/out.txt" || fail "deploy 404: no clear message"
[[ "$(grep -c "exec -T web wget" "$root/docker.log")" == "1" ]] || fail "deploy 404: probe retried"

# 4. rollback to an image without /api/ready: warns, succeeds.
root="$(setup_root rollback-old)"
code="$(run "$root" 404 rollback.sh v1.0.0)"
[[ "$code" == "0" ]] || fail "rollback 404: exit $code, expected 0"
grep -q "predates /api/ready" "$root/out.txt" || fail "rollback 404: no warning"

# 5. rollback, backend unreachable: fails with a clear message.
root="$(setup_root rollback-unreachable)"
code="$(run "$root" fail rollback.sh v1.0.0)"
[[ "$code" != "0" ]] || fail "rollback unreachable: exit 0, expected failure"
grep -q "still cannot reach the API" "$root/out.txt" || fail "rollback unreachable: no clear message"

# 6. rollback, backend reachable: success.
root="$(setup_root rollback-ok)"
code="$(run "$root" ok rollback.sh v1.0.0)"
[[ "$code" == "0" ]] || fail "rollback ok: exit $code, expected 0"

if ((failures > 0)); then
  echo "$failures deploy-script test(s) failed." >&2
  exit 1
fi
echo "OK: deploy/rollback readiness checks behave as expected (6 scenarios)."
