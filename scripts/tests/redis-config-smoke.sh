#!/usr/bin/env bash
# Starts only the redis service of the rendered production config, under its own
# Compose project (never the stack's), and checks what check-compose-ports.sh
# can only read from the config: the healthcheck passes with the password, an
# anonymous client is refused, maxmemory / noeviction / AOF are in effect, the
# password is not in the process arguments, Redis runs as the redis user, and a
# full Redis refuses writes instead of evicting. Removes its project and volume.
#
# Usage: scripts/tests/redis-config-smoke.sh [ENV_FILE]   (default: ./.env)
# Needs Docker Compose, the variables the prod overlay requires (CI sets them)
# and REDIS_PASSWORD.
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="${1:-$root_dir/.env}"
project="betpulse-redis-smoke-$$"
if [[ -z "${REDIS_PASSWORD:-}" ]]; then
  echo "Set REDIS_PASSWORD (letters and digits) to run this smoke test." >&2
  exit 1
fi

# Its own subnets: the stack's fixed ones may already exist on this host.
export BETPULSE_NETWORK_SUBNET=172.29.199.0/24 BETPULSE_NETWORK_GATEWAY=172.29.199.1
export BETPULSE_NETWORK_SUBNET6=fd42:b7e1:5a29:199::/64 BETPULSE_NETWORK_GATEWAY6=fd42:b7e1:5a29:199::1
export BETPULSE_WEB_IP=172.29.199.10 BETPULSE_CADDY_IP=172.29.199.11
export BETPULSE_WEB_IP6=fd42:b7e1:5a29:199::10 BETPULSE_CADDY_IP6=fd42:b7e1:5a29:199::11

compose() {
  docker compose -p "$project" --env-file "$env_file" \
    -f "$root_dir/infra/docker-compose.yml" \
    -f "$root_dir/infra/docker-compose.prod.yml" "$@"
}
trap 'compose down -v --remove-orphans >/dev/null 2>&1 || true' EXIT

failures=0
fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}
ok() { echo "ok   $*"; }

compose up -d --no-deps redis >/dev/null
container="$(compose ps -q redis)"

health=""
for _ in $(seq 1 30); do
  health="$(docker inspect -f '{{.State.Health.Status}}' "$container")"
  [[ "$health" == "healthy" ]] && break
  sleep 2
done
if [[ "$health" == "healthy" ]]; then ok "healthcheck passes with the password"; else fail "redis is $health"; fi

# Every command runs inside the container; the password never leaves it.
in_redis() { docker exec "$container" sh -c "$1"; }
authed() { in_redis "REDISCLI_AUTH=\"\$REDIS_PASSWORD\" redis-cli $1"; }

anonymous="$(in_redis 'redis-cli ping' 2>&1 || true)"
if [[ "$anonymous" == *NOAUTH* ]]; then ok "an anonymous client is refused"; else fail "anonymous ping answered: $anonymous"; fi

expect_config() {
  local key="$1" want="$2" got
  got="$(authed "config get $key" | tail -n 1)"
  if [[ "$got" == "$want" ]]; then ok "$key = $want"; else fail "$key is '$got', expected '$want'"; fi
}
expect_config maxmemory 268435456
expect_config maxmemory-policy noeviction
expect_config appendonly yes
expect_config appendfsync everysec

# shellcheck disable=SC2016 # both expanded by the container's shell, on purpose
if ! in_redis '[ -n "${REDIS_PASSWORD:-}" ]'; then
  fail "the redis container has no REDIS_PASSWORD"
elif in_redis 'tr "\0" " " </proc/1/cmdline | grep -qF -- "$REDIS_PASSWORD"'; then
  fail "the password is in the process arguments"
else
  ok "the password is not in the process arguments"
fi
user="$(in_redis 'stat -c %U /proc/1')"
if [[ "$user" == "redis" ]]; then ok "redis-server runs as redis"; else fail "redis-server runs as $user"; fi

# noeviction: once full, a write fails (OOM) and nothing is evicted.
authed "set smoke:ttl kept EX 600" >/dev/null
authed "config set maxmemory 1" >/dev/null
refused="$(authed "set smoke:new value" 2>&1 || true)"
authed "config set maxmemory 256mb" >/dev/null
if [[ "$refused" == *OOM* ]]; then ok "a full Redis refuses writes"; else fail "a write over maxmemory answered: $refused"; fi
if [[ "$(authed "get smoke:ttl")" == "kept" ]]; then ok "no key with a TTL was evicted"; else fail "smoke:ttl was evicted"; fi

if ((failures > 0)); then
  echo "$failures Redis smoke check(s) failed." >&2
  exit 1
fi
echo "OK: Redis requires its password, runs noeviction under maxmemory with AOF, and refuses writes when full."
