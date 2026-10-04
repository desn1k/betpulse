#!/usr/bin/env bash
# PUBLIC_DOMAIN must reach the Caddy container through the real production
# Compose config (base + prod overlay), not through a `docker run -e`. The
# Caddyfile's site address is {$PUBLIC_DOMAIN:localhost}, so a container without
# the variable silently serves only `localhost` and never gets a certificate for
# the real domain.
#
# Starts only the caddy service (--no-deps) of the rendered prod config under a
# throwaway Compose project and checks, for each domain:
#   1. `compose config` refuses an empty PUBLIC_DOMAIN;
#   2. PUBLIC_DOMAIN=example.test: `printenv` in the container, the site hosts in
#      Caddy's live config (admin API) and `curl --resolve` over HTTP (308 to
#      https://example.test/). example.test cannot get a certificate offline:
#      Caddy goes to ACME for it;
#   3. PUBLIC_DOMAIN=betpulse.localhost: `curl --resolve` over HTTPS answers
#      /healthz. Caddy issues *.localhost certificates from its internal CA, so
#      this proves TLS is served for the configured name; with the variable
#      missing the handshake fails (the site would be `localhost`).
#
# Publishes host ports 80/443 like production, so nothing else may hold them.
# Needs a repo-root .env (copied from .env.example in CI) and POSTGRES_PASSWORD
# (required by the base file's interpolation). Run in CI.
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${1:-$root_dir/.env}"
project="caddy-domain-smoke-$$"
ready_attempts="${CADDY_DOMAIN_SMOKE_ATTEMPTS:-30}"
# Every compose call interpolates the overlay's required ${PUBLIC_DOMAIN:?},
# even exec/logs/down, so they all get the domain under test.
domain="example.test"

compose() {
  PUBLIC_DOMAIN="$domain" docker compose -p "$project" --env-file "$env_file" \
    -f "$root_dir/infra/docker-compose.yml" \
    -f "$root_dir/infra/docker-compose.prod.yml" "$@"
}

cleanup() {
  compose down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  compose logs caddy >&2 || true
  exit 1
}

if out="$(domain="" compose config --quiet 2>&1)"; then
  fail "compose config accepted an empty PUBLIC_DOMAIN"
fi
# Any other failure (a missing POSTGRES_PASSWORD, a missing env file) must not
# pass for the PUBLIC_DOMAIN guard.
[[ "$out" == *"PUBLIC_DOMAIN is required"* ]] ||
  fail "compose config failed, but not on PUBLIC_DOMAIN: $out"
echo "ok: compose config refuses an empty PUBLIC_DOMAIN"

start_caddy() {
  domain="$1"
  local out attempt
  if ! out="$(compose up -d --no-deps --force-recreate caddy 2>&1)"; then
    echo "$out" >&2
    fail "compose up caddy failed with PUBLIC_DOMAIN=$1"
  fi
  for ((attempt = 1; attempt <= ready_attempts; attempt++)); do
    if compose exec -T caddy wget -q -O - http://127.0.0.1:8081/healthz >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  fail "caddy did not start with PUBLIC_DOMAIN=$1"
}

# The site hosts of Caddy's running config, e.g. ["example.test"].
site_hosts() {
  compose exec -T caddy wget -q -O - http://127.0.0.1:2019/config/apps/http/servers |
    grep -o '"host":\[[^]]*\]' | sort -u | tr -d '\n'
}

start_caddy example.test
env_value="$(compose exec -T caddy printenv PUBLIC_DOMAIN || true)"
[[ "$env_value" == "example.test" ]] || fail "printenv PUBLIC_DOMAIN in caddy is '$env_value'"
echo "ok: printenv PUBLIC_DOMAIN = example.test"
hosts="$(site_hosts || true)"
[[ "$hosts" == '"host":["example.test"]' ]] || fail "Caddy site hosts are $hosts"
echo "ok: Caddy serves the site for example.test"
redirect="$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' \
  --max-time 5 --resolve example.test:80:127.0.0.1 http://example.test/healthz || true)"
[[ "$redirect" == "308 https://example.test/healthz" ]] || fail "http://example.test answered '$redirect'"
echo "ok: http://example.test redirects to https://example.test"

start_caddy betpulse.localhost
body=""
for ((attempt = 1; attempt <= ready_attempts; attempt++)); do
  # -k: the certificate comes from Caddy's internal CA, not a public one.
  if body="$(curl -sk --max-time 5 --resolve betpulse.localhost:443:127.0.0.1 \
    https://betpulse.localhost/healthz)" && [[ "$body" == "ok" ]]; then
    break
  fi
  sleep 1
done
[[ "$body" == "ok" ]] || fail "https://betpulse.localhost/healthz answered '$body'"
echo "ok: https://betpulse.localhost serves /healthz with a certificate for that name"

echo "OK: PUBLIC_DOMAIN reaches Caddy through the production Compose config."
