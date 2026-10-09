#!/usr/bin/env bash
# Client identity at the edge (F5): an IPv4 and an IPv6 client must reach the
# upstream behind Caddy under their own addresses, never as the Docker bridge
# gateway.
#
# Runs the production Compose config (base + prod overlay) with caddy as is and
# `web` replaced by a stub that echoes the X-Forwarded-For Caddy sent it and the
# address Caddy connected from. The client lives in its own network namespace,
# joined to the host by a veth pair, so its packets enter the host through an
# ordinary interface the way an outside client's do, and reach Caddy through
# Docker's port publishing. (A client container on another bridge is no model
# for that: Docker hands container-to-host connections to its userland proxy,
# which hides even IPv4 sources.)
#
# Before F5 the project network was IPv4-only, so Docker handed IPv6
# connections to its userland proxy and Caddy saw them coming from the
# network's gateway (172.29.89.1): every IPv6 guest was one identity.
#
# Linux Docker only, and needs passwordless sudo (CI runners have it): Docker
# Desktop forwards every published port through its own proxy, so there even
# IPv4 clients arrive as the gateway (HANDOFF F5). Publishes host ports 80/443
# like production, so nothing else may hold them. Needs a repo-root .env
# (copied from .env.example in CI) and POSTGRES_PASSWORD (required by the base
# file's interpolation). Run in CI.
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${1:-$root_dir/.env}"
caddy_image="${CADDY_IMAGE:-caddy:2.10-alpine@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d}"
ready_attempts="${EDGE_IDENTITY_SMOKE_ATTEMPTS:-30}"
project="edge-identity-smoke-$$"
work="$(mktemp -d)"

# The client's link to the host: addresses outside the project network, so a
# preserved source can never be mistaken for a project address. The host end
# holds the .1 / ::1 addresses the client connects to.
netns="f5probe$$"
host_if="f5h$$"
client_if="f5c$$"
host_ip4="10.253.53.1"
client_ip4="10.253.53.10"
host_ip6="fd42:b7e1:5a29:fe::1"
client_ip6="fd42:b7e1:5a29:fe::10"

# Caddy's pinned addresses (infra/docker-compose*.yml): the stub must see the
# connection come from one of them, the hop the API's TRUSTED_PROXY_CIDRS names.
caddy_ip4="${BETPULSE_CADDY_IP:-172.29.89.11}"
caddy_ip6="${BETPULSE_CADDY_IP6:-fd42:b7e1:5a29:89::11}"

# Only caddy and the stub run; the release variables are required to render
# the overlay but nothing is pulled from GHCR.
export PUBLIC_DOMAIN=":80" GHCR_OWNER="${GHCR_OWNER:-desn1k}" IMAGE_TAG="${IMAGE_TAG:-v0.0.0-ci}"
export API_IMAGE_DIGEST="${API_IMAGE_DIGEST:-sha256:$(printf '%064d' 1)}"
export WEB_IMAGE_DIGEST="${WEB_IMAGE_DIGEST:-sha256:$(printf '%064d' 2)}"
export MLFLOW_IMAGE_DIGEST="${MLFLOW_IMAGE_DIGEST:-sha256:$(printf '%064d' 3)}"

# The stub replaces only the web image and command; its network entry (the
# pinned addresses) comes from the real files.
cat >"$work/stub.yml" <<YAML
services:
  web:
    image: $caddy_image
    entrypoint: ["sh", "-c"]
    command:
      - printf ':3000 {\n\trespond "xff={header.X-Forwarded-For} peer={remote_host}"\n}\n' > /tmp/Caddyfile && exec caddy run --config /tmp/Caddyfile --adapter caddyfile
YAML

compose() {
  docker compose -p "$project" --env-file "$env_file" \
    -f "$root_dir/infra/docker-compose.yml" \
    -f "$root_dir/infra/docker-compose.prod.yml" \
    -f "$work/stub.yml" "$@"
}

cleanup() {
  sudo ip netns del "$netns" >/dev/null 2>&1 || true
  sudo ip link del "$host_if" >/dev/null 2>&1 || true
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  compose logs caddy web >&2 || true
  exit 1
}

echo "Docker $(docker version --format '{{.Server.Version}}'), $(docker info --format '{{.OperatingSystem}}')"

compose up -d --no-deps web caddy

sudo ip netns add "$netns"
sudo ip link add "$host_if" type veth peer name "$client_if"
sudo ip link set "$client_if" netns "$netns"
sudo ip addr add "$host_ip4/24" dev "$host_if"
sudo ip -6 addr add "$host_ip6/64" dev "$host_if" nodad
sudo ip link set "$host_if" up
sudo ip netns exec "$netns" ip link set lo up
sudo ip netns exec "$netns" ip addr add "$client_ip4/24" dev "$client_if"
sudo ip netns exec "$netns" ip -6 addr add "$client_ip6/64" dev "$client_if" nodad
sudo ip netns exec "$netns" ip link set "$client_if" up

# fetch URL -> the stub's answer through Caddy, from inside the namespace.
fetch() {
  sudo ip netns exec "$netns" curl -sS -g --max-time 5 "$1" 2>&1
}

ready=false
for ((attempt = 1; attempt <= ready_attempts; attempt++)); do
  if [[ "$(fetch "http://$host_ip4/" || true)" == xff=* ]]; then
    ready=true
    break
  fi
  sleep 1
done
[[ "$ready" == true ]] || fail "caddy and the web stub did not answer over IPv4: $(fetch "http://$host_ip4/" || true)"

failures=0
check() {
  local family="$1" url="$2" client="$3" answer
  answer="$(fetch "$url" || true)"
  if [[ "$answer" == "xff=$client peer=$caddy_ip4" || "$answer" == "xff=$client peer=$caddy_ip6" ]]; then
    echo "ok   $family client $client reaches the upstream as itself ($answer)"
  else
    echo "FAIL $family client $client: upstream got '$answer' (expected xff=$client from caddy $caddy_ip4 or $caddy_ip6)" >&2
    failures=$((failures + 1))
  fi
}

check IPv4 "http://$host_ip4/" "$client_ip4"
check IPv6 "http://[$host_ip6]/" "$client_ip6"

if ((failures > 0)); then
  echo "$failures client identity check(s) failed: those clients would share the gateway's identity (F5)." >&2
  compose logs caddy >&2 || true
  exit 1
fi
echo "OK: IPv4 and IPv6 clients reach the upstream under their own addresses."
