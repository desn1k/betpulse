#!/usr/bin/env bash
# Fail unless the rendered production Compose configuration publishes only
# Caddy's 80/tcp and 443/tcp. Every other service (Postgres, Redis, MinIO,
# MLflow, the API, the web app) must stay on the internal network.
#
# Why a rendered check: in an override file `ports: []` is *appended* to the
# base file's list, so it does not remove anything; only `ports: !reset []`
# does. Checking the merged result also catches a Compose version that handles
# the tag differently. Ports published by Docker bypass ufw, so this is the
# real exposure of the host.
#
# Usage: scripts/check-compose-ports.sh [ENV_FILE]   (default: ./.env)
# Run in CI and on the server after installing or upgrading Docker. Needs
# python3 (preinstalled on Ubuntu); PYTHON overrides the interpreter. The
# services' `env_file: ../.env` means the repo-root .env must exist as well,
# whatever ENV_FILE is (on the server they are the same file).
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${1:-$root_dir/.env}"
python_bin="${PYTHON:-python3}"

for required in "$env_file" "$root_dir/.env"; do
  if [[ ! -f "$required" ]]; then
    echo "Missing env file: $required" >&2
    exit 1
  fi
done

rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT
docker compose --env-file "$env_file" \
  -f "$root_dir/infra/docker-compose.yml" \
  -f "$root_dir/infra/docker-compose.prod.yml" \
  config --format json >"$rendered"

"$python_bin" - "$rendered" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    config = json.load(fh)

allowed = {("caddy", "80", "tcp"), ("caddy", "443", "tcp")}
published = set()
problems = []
for name, service in sorted(config.get("services", {}).items()):
    if service.get("network_mode") == "host":
        problems.append(f"{name}: network_mode host exposes every port it listens on")
    for port in service.get("ports") or []:
        key = (name, str(port.get("published")), port.get("protocol") or "tcp")
        host_ip = port.get("host_ip") or "0.0.0.0"
        published.add(key)
        if key not in allowed:
            problems.append(f"{name}: {host_ip}:{key[1]} -> {port.get('target')}/{key[2]}")

for name, port, proto in sorted(allowed - published):
    problems.append(f"{name}: expected {port}/{proto} to be published")

if problems:
    print("Production Compose config publishes unexpected ports:", file=sys.stderr)
    for line in problems:
        print(f"  {line}", file=sys.stderr)
    sys.exit(1)
print("OK: only caddy 80/tcp and 443/tcp are published.")
PY
