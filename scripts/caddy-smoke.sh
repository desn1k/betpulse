#!/usr/bin/env bash
# Routing smoke test for infra/Caddyfile: runs the real Caddy config in front of
# two stub upstreams (reachable as `api:8000` and `web:3000`, like in Compose)
# and asserts which upstream answers each public path. Needs only Docker; every
# container and the network are removed on exit.
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
caddy_image="${CADDY_IMAGE:-caddy:2.9-alpine}"
ready_attempts="${CADDY_SMOKE_READY_ATTEMPTS:-40}"

prefix="caddy-smoke-$$-$RANDOM"
network="$prefix-net"
proxy="$prefix-proxy"

cleanup() {
  docker rm -f "$proxy" "$prefix-api" "$prefix-web" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$network" >/dev/null

start_stub() {
  local name="$1" port="$2"
  docker run -d --name "$prefix-$name" --network "$network" --network-alias "$name" \
    "$caddy_image" caddy respond --listen ":$port" --body "upstream=$name" >/dev/null
}

start_stub api 8000
start_stub web 3000

# PUBLIC_DOMAIN=":80" keeps the site block identical but serves plain HTTP, so
# the test exercises routing without ACME/TLS.
docker run -d --name "$proxy" --network "$network" \
  -e PUBLIC_DOMAIN=":80" \
  -v "$root_dir/infra/Caddyfile:/etc/caddy/Caddyfile:ro" \
  "$caddy_image" >/dev/null

# fetch METHOD PATH -> response body (busybox wget inside the proxy container).
fetch() {
  local method="$1" path="$2"
  if [[ "$method" == "POST" ]]; then
    docker exec "$proxy" wget -q -O - --post-data '{}' "http://localhost$path" 2>/dev/null
  else
    docker exec "$proxy" wget -q -O - "http://localhost$path" 2>/dev/null
  fi
}

ready=false
for ((attempt = 1; attempt <= ready_attempts; attempt++)); do
  if [[ "$(fetch GET /healthz || true)" == "ok" &&
    "$(fetch GET / || true)" == "upstream=web" &&
    "$(fetch POST /push/telegram/webhook || true)" == "upstream=api" ]]; then
    ready=true
    break
  fi
  sleep 0.5
done
if [[ "$ready" != true ]]; then
  echo "Caddy or stub upstreams did not become ready." >&2
  docker logs "$proxy" >&2 || true
  exit 1
fi

failures=0
expect() {
  local method="$1" path="$2" expected="$3" actual
  actual="$(fetch "$method" "$path" || echo "<request failed>")"
  if [[ "$actual" == "$expected" ]]; then
    echo "ok   $method $path -> $actual"
  else
    echo "FAIL $method $path -> $actual (expected $expected)" >&2
    failures=$((failures + 1))
  fi
}

expect GET /healthz ok

# The only FastAPI path exposed publicly.
expect POST /push/telegram/webhook upstream=api

# Next.js BFF route handlers, including the ones whose FastAPI counterpart
# shares the /push/telegram prefix.
expect GET /api/health upstream=web
expect GET "/api/matches?league=EPL" upstream=web
expect POST /api/auth/login upstream=web
expect GET /push/telegram/link upstream=web
expect POST /push/telegram/link upstream=web
expect POST /push/telegram upstream=web
expect POST /push/telegram/webhook/ upstream=web
expect POST /push/telegram/webhook-extra upstream=web

# FastAPI docs and internal routes must not be reachable through the edge.
expect GET /docs upstream=web
expect GET /docs/oauth2-redirect upstream=web
expect GET /openapi.json upstream=web
expect GET /admin/users upstream=web
expect GET /matches upstream=web

# Pages.
expect GET / upstream=web
expect GET /matches/123 upstream=web

if ((failures > 0)); then
  echo "$failures Caddy routing assertion(s) failed." >&2
  exit 1
fi
echo "Caddy routing smoke test passed."
