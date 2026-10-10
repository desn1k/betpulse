#!/usr/bin/env bash
# Behaviour tests for scripts/check-compose-ports.sh's network checks (F5): the
# real production config passes, and each override that breaks the dual-stack
# network, a pinned proxy address or the API's address settings fails with its
# own message; likewise each Redis override (no password, an evicting policy, no
# AOF, a client without the password). Needs Docker Compose and python3, a
# repo-root .env (copied from .env.example in CI) and POSTGRES_PASSWORD.
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="${1:-$root_dir/.env}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Required to render the prod overlay; nothing is pulled or started.
export PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-betpulse.example.test}" GHCR_OWNER="${GHCR_OWNER:-desn1k}"
export IMAGE_TAG="${IMAGE_TAG:-v0.0.0-ci}"
# Letters and digits only: Compose renders it into REDIS_URL.
export REDIS_PASSWORD="${REDIS_PASSWORD:-ci0redis0password0a1b2c3d4e5f6a7b8c9d0e1f2}"
export API_IMAGE_DIGEST="${API_IMAGE_DIGEST:-sha256:$(printf '%064d' 1)}"
export WEB_IMAGE_DIGEST="${WEB_IMAGE_DIGEST:-sha256:$(printf '%064d' 2)}"
export MLFLOW_IMAGE_DIGEST="${MLFLOW_IMAGE_DIGEST:-sha256:$(printf '%064d' 3)}"

failures=0
fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}

if out="$(bash "$root_dir/scripts/check-compose-ports.sh" "$env_file" 2>&1)"; then
  echo "ok   the production config passes"
else
  fail "the production config is rejected: $out"
fi

# expect_rejected NAME MESSAGE: the override in $work/NAME.yml must make the
# check fail, and its output must name the problem.
expect_rejected() {
  local name="$1" message="$2" out
  if out="$(CHECK_EXTRA_COMPOSE_FILE="$work/$name.yml" \
    bash "$root_dir/scripts/check-compose-ports.sh" "$env_file" 2>&1)"; then
    fail "$name: accepted"
  elif [[ "$out" != *"$message"* ]]; then
    fail "$name: rejected without '$message': $out"
  else
    echo "ok   $name rejected ($message)"
  fi
}

cat >"$work/ipv4-only-network.yml" <<'YAML'
networks:
  default:
    enable_ipv6: false
YAML
expect_rejected ipv4-only-network "network default: enable_ipv6 is not true"

cat >"$work/caddy-without-ipv6.yml" <<'YAML'
services:
  caddy:
    networks: !override
      default:
        ipv4_address: 172.29.89.11
YAML
expect_rejected caddy-without-ipv6 "caddy: no pinned IPv6 address"

cat >"$work/web-outside-subnet.yml" <<'YAML'
services:
  web:
    networks:
      default:
        ipv6_address: fd00:1::10
YAML
expect_rejected web-outside-subnet "web: IPv6 address fd00:1::10 is outside"

cat >"$work/caddy-malformed-ipv6.yml" <<'YAML'
services:
  caddy:
    networks:
      default:
        ipv6_address: not-an-address
YAML
expect_rejected caddy-malformed-ipv6 "caddy: invalid IPv6 address 'not-an-address'"

cat >"$work/api-trusts-ipv4-only.yml" <<'YAML'
services:
  api:
    environment:
      TRUSTED_PROXY_CIDRS: 172.29.89.10/32,172.29.89.11/32
YAML
expect_rejected api-trusts-ipv4-only "api: TRUSTED_PROXY_CIDRS"

cat >"$work/worker-internal-ipv4-only.yml" <<'YAML'
services:
  worker-batch:
    environment:
      INTERNAL_NETWORK_CIDRS: 172.29.89.0/24
YAML
expect_rejected worker-internal-ipv4-only "worker-batch: INTERNAL_NETWORK_CIDRS"

# Redis: a password on the server and in every client's REDIS_URL, a memory
# limit that never evicts, and AOF persistence.
cat >"$work/redis-without-password.yml" <<'YAML'
services:
  redis:
    environment:
      REDIS_PASSWORD: ""
YAML
expect_rejected redis-without-password "redis: REDIS_PASSWORD is empty"

cat >"$work/redis-evicting.yml" <<'YAML'
services:
  redis:
    command: !override ["redis-server", "--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lru", "--appendonly", "yes"]
YAML
expect_rejected redis-evicting "redis: --maxmemory-policy is 'allkeys-lru', expected 'noeviction'"

cat >"$work/redis-without-aof.yml" <<'YAML'
services:
  redis:
    command: !override ["redis-server", "--maxmemory", "256mb", "--maxmemory-policy", "noeviction"]
YAML
expect_rejected redis-without-aof "redis: --appendonly is None, expected 'yes'"

cat >"$work/redis-unbounded.yml" <<'YAML'
services:
  redis:
    command: !override ["redis-server", "--maxmemory-policy", "noeviction", "--appendonly", "yes"]
YAML
expect_rejected redis-unbounded "redis: no --maxmemory"

cat >"$work/redis-healthcheck-without-auth.yml" <<'YAML'
services:
  redis:
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
YAML
expect_rejected redis-healthcheck-without-auth "redis: the healthcheck does not authenticate"

cat >"$work/api-redis-without-password.yml" <<'YAML'
services:
  api:
    environment:
      REDIS_URL: redis://redis:6379/0
YAML
expect_rejected api-redis-without-password "api: REDIS_URL carries no password"

cat >"$work/worker-redis-wrong-password.yml" <<'YAML'
services:
  worker-ml:
    environment:
      REDIS_URL: redis://:0123wrong4567@redis:6379/0
YAML
expect_rejected worker-redis-wrong-password "worker-ml: REDIS_URL password differs from redis's REDIS_PASSWORD"

cat >"$work/worker-redis-elsewhere.yml" <<'YAML'
services:
  worker-batch:
    environment:
      REDIS_URL: redis://:x@cache.example:6379/0
YAML
expect_rejected worker-redis-elsewhere "worker-batch: REDIS_URL points at cache.example:6379"

# The closed-trial certificate switch (CADDY_TRIAL_TLS): off by default and said
# so; local_certs (Caddy's internal CA) is reported; any other value is refused.
if out="$(bash "$root_dir/scripts/check-compose-ports.sh" "$env_file" 2>&1)" &&
  [[ "$out" == *"caddy: public certificates (ACME)"* ]]; then
  echo "ok   the default is public certificates, and the check says so"
else
  fail "default certificate mode not reported as ACME: $out"
fi
if out="$(CADDY_TRIAL_TLS=local_certs bash "$root_dir/scripts/check-compose-ports.sh" "$env_file" 2>&1)" &&
  [[ "$out" == *"caddy: CLOSED TRIAL"* ]]; then
  echo "ok   CADDY_TRIAL_TLS=local_certs passes and is reported as the closed trial"
else
  fail "CADDY_TRIAL_TLS=local_certs not reported: $out"
fi
if out="$(CADDY_TRIAL_TLS=internal bash "$root_dir/scripts/check-compose-ports.sh" "$env_file" 2>&1)"; then
  fail "CADDY_TRIAL_TLS=internal accepted"
elif [[ "$out" != *"caddy: CADDY_TRIAL_TLS must be empty or local_certs"* ]]; then
  fail "CADDY_TRIAL_TLS=internal rejected without the message: $out"
else
  echo "ok   CADDY_TRIAL_TLS=internal rejected"
fi

if ((failures > 0)); then
  echo "$failures check-compose-ports test(s) failed." >&2
  exit 1
fi
echo "OK: check-compose-ports.sh accepts the dual-stack network and rejects every broken variant."
