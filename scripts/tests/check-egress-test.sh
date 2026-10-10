#!/usr/bin/env bash
# Behaviour tests for scripts/check-egress.sh with `curl`, `getent` and `ip`
# replaced by stubs on PATH (no network). The stubs:
#   getent ahostsv4|ahostsv6 HOST: an address, none for ahostsv6 when HOST is in
#     STUB_NO_AAAA;
#   curl -4|-6 ... https://HOST/: exit 7 (cannot connect) when HOST is in
#     STUB_FAIL4 / STUB_FAIL6, else prints STUB_STATUS_<n> for the host whose
#     name is in STUB_STATUS_HOST, or 404;
#   ip -6 route show default: a route unless STUB_NO_V6ROUTE=1.
set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

cat >"$work/bin/getent" <<'STUB'
#!/usr/bin/env bash
host="$2"
if [[ "$1" == "ahostsv6" ]]; then
  [[ " ${STUB_NO_AAAA:-} " == *" $host "* ]] && exit 2
  echo "2001:db8::1     STREAM $host"
else
  echo "192.0.2.1       STREAM $host"
fi
STUB
cat >"$work/bin/curl" <<'STUB'
#!/usr/bin/env bash
family=""
url=""
for arg in "$@"; do
  case "$arg" in
    -4) family=4 ;;
    -6) family=6 ;;
    https://*) url="$arg" ;;
  esac
done
host="${url#https://}"
host="${host%%/*}"
fails="STUB_FAIL$family"
if [[ " ${!fails:-} " == *" $host "* ]]; then
  echo "curl: (7) Failed to connect to $host" >&2
  printf '000'
  exit 7
fi
if [[ -n "${STUB_STATUS_HOST:-}" && "$host" == "$STUB_STATUS_HOST" ]]; then
  printf '%s' "$STUB_STATUS"
else
  printf '404'
fi
STUB
cat >"$work/bin/ip" <<'STUB'
#!/usr/bin/env bash
[[ "${STUB_NO_V6ROUTE:-0}" == "1" ]] || echo "default via fe80::1 dev eth0 proto ra metric 100"
STUB
chmod +x "$work/bin/"*

failures=0
fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}

# run: the script under the stubs; sets $code and $out.
run() {
  set +e
  out="$(PATH="$work/bin:$PATH" bash "$repo_dir/scripts/check-egress.sh" 2>&1)"
  code=$?
  set -e
}

required=(ghcr.io pkg-containers.githubusercontent.com registry-1.docker.io api.sportmonks.com
  api.the-odds-api.com api.telegram.org fcm.googleapis.com web.push.apple.com
  updates.push.services.mozilla.com api.openai.com)

# 1. Everything reachable over both families.
run
[[ "$code" == "0" ]] || fail "all reachable: exit $code, expected 0: $out"
for host in "${required[@]}"; do
  grep -qE "^$host +ok \(404\) +ok \(404\)" <<<"$out" || fail "all reachable: no ok row for $host"
done
grep -q "IPv4: every host reachable" <<<"$out" || fail "all reachable: no IPv4 summary"
grep -q "IPv6: every host with an AAAA record reachable" <<<"$out" || fail "all reachable: no IPv6 summary"

# 2. A required host unreachable over IPv4: exit 1, named.
STUB_FAIL4="ghcr.io" run
[[ "$code" == "1" ]] || fail "ghcr.io IPv4 down: exit $code, expected 1"
grep -qE "^ghcr.io +FAIL" <<<"$out" || fail "ghcr.io IPv4 down: no FAIL row"
grep -q "IPv4 unreachable: ghcr.io" <<<"$out" || fail "ghcr.io IPv4 down: not in the summary"

# 3. IPv6 only fails: a warning, exit 0.
STUB_FAIL6="updates.push.services.mozilla.com" run
[[ "$code" == "0" ]] || fail "mozilla IPv6 down: exit $code, expected 0"
grep -q "WARN: IPv6 unreachable: updates.push.services.mozilla.com" <<<"$out" ||
  fail "mozilla IPv6 down: no warning"

# 4. A host without AAAA is not an IPv6 failure.
STUB_NO_AAAA="api.sportmonks.com" run
[[ "$code" == "0" ]] || fail "no AAAA: exit $code, expected 0"
grep -qE "^api.sportmonks.com +ok \(404\) +no AAAA" <<<"$out" || fail "no AAAA: row not marked"
grep -q "IPv6: every host with an AAAA record reachable" <<<"$out" || fail "no AAAA: counted as a failure"

# 5. The server has no IPv6 route: IPv6 skipped with a warning.
STUB_NO_V6ROUTE=1 run
[[ "$code" == "0" ]] || fail "no IPv6 route: exit $code, expected 0"
grep -q "WARN: this server has no IPv6 default route" <<<"$out" || fail "no IPv6 route: no warning"
grep -qE "^ghcr.io +ok \(404\) +skipped" <<<"$out" || fail "no IPv6 route: IPv6 not skipped"

# 6. A 403 (e.g. a provider that refuses this region) is reported, not hidden.
STUB_STATUS_HOST="api.openai.com" STUB_STATUS=403 run
grep -qE "^api.openai.com +403\?" <<<"$out" || fail "403: row not marked"
grep -q "WARN: api.openai.com answered 403" <<<"$out" || fail "403: no warning"

# 7. LLM_HOST replaces the default LLM host.
LLM_HOST="llm.example.test" run
grep -qE "^llm.example.test +ok" <<<"$out" || fail "LLM_HOST: the configured host is not checked"
if grep -qE "^api.openai.com " <<<"$out"; then fail "LLM_HOST: the default host is still checked"; fi

if ((failures > 0)); then
  echo "$failures check-egress test(s) failed." >&2
  exit 1
fi
echo "OK: check-egress.sh reports every host per family and fails only on an IPv4-unreachable host."
