#!/usr/bin/env bash
# Behaviour tests for scripts/check-compose-ports.sh's network checks (F5): the
# real production config passes, and each override that breaks the dual-stack
# network, a pinned proxy address or the API's address settings fails with its
# own message. Needs Docker Compose and python3, a repo-root .env (copied from
# .env.example in CI) and POSTGRES_PASSWORD.
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_file="${1:-$root_dir/.env}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Required to render the prod overlay; nothing is pulled or started.
export PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-betpulse.example.test}" GHCR_OWNER="${GHCR_OWNER:-desn1k}"
export IMAGE_TAG="${IMAGE_TAG:-v0.0.0-ci}"
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

if ((failures > 0)); then
  echo "$failures check-compose-ports test(s) failed." >&2
  exit 1
fi
echo "OK: check-compose-ports.sh accepts the dual-stack network and rejects every broken variant."
