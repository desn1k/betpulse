#!/usr/bin/env bash
# Fail unless the rendered production Compose configuration publishes only
# Caddy's 80/tcp and 443/tcp. Every other service (Postgres, Redis, MLflow,
# the API, the web app) must stay on the internal network. It also
# checks that the web container reaches the API at http://api:8000 whatever
# API_BASE_URL says in .env (its example value is for local dev), that caddy
# gets PUBLIC_DOMAIN (else the Caddyfile serves only `localhost`), that no
# environment value is a comment (Compose reads `KEY=  # note` as `# note`), and
# that every image is pinned by digest (`@sha256:...`) and none is built on the
# server: a tag alone can be re-pushed (ER-H-09). Since F5 it also checks the
# dual-stack network: IPv6 enabled with one subnet per family, web and caddy
# pinned in both, and TRUSTED_PROXY_CIDRS / INTERNAL_NETWORK_CIDRS of the API
# and every worker naming exactly those addresses and subnets. It also checks
# Redis: requirepass from a non-empty REDIS_PASSWORD, the same password in the
# REDIS_URL of the API and every worker, maxmemory with noeviction, AOF on, and
# a healthcheck that authenticates.
#
# Why a rendered check: in an override file `ports: []` is *appended* to the
# base file's list, so it does not remove anything; only `ports: !reset []`
# does. Checking the merged result also catches a Compose version that handles
# the tag differently. Ports published by Docker bypass ufw, so this is the
# real exposure of the host.
#
# Usage: scripts/check-compose-ports.sh [ENV_FILE]   (default: ./.env)
# CHECK_EXTRA_COMPOSE_FILE adds one more -f file (CI uses it to prove the
# checks fail on a bad override).
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
compose_files=(-f "$root_dir/infra/docker-compose.yml" -f "$root_dir/infra/docker-compose.prod.yml")
if [[ -n "${CHECK_EXTRA_COMPOSE_FILE:-}" ]]; then
  compose_files+=(-f "$CHECK_EXTRA_COMPOSE_FILE")
fi
docker compose --env-file "$env_file" "${compose_files[@]}" config --format json >"$rendered"

"$python_bin" - "$rendered" <<'PY'
import json
import re
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

web_env = config.get("services", {}).get("web", {}).get("environment") or {}
if isinstance(web_env, list):
    web_env = dict(item.split("=", 1) for item in web_env)
api_url = web_env.get("API_BASE_URL")
if api_url != "http://api:8000":
    problems.append(f"web: API_BASE_URL is {api_url!r}, expected 'http://api:8000'")

caddy_env = config.get("services", {}).get("caddy", {}).get("environment") or {}
if isinstance(caddy_env, list):
    caddy_env = dict(item.split("=", 1) for item in caddy_env if "=" in item)
if not (caddy_env.get("PUBLIC_DOMAIN") or "").strip():
    # Without it the Caddyfile falls back to `localhost`: no certificate for the
    # real domain.
    problems.append("caddy: PUBLIC_DOMAIN is not passed to the container")
# The closed-trial certificate switch (infra/Caddyfile): empty = public ACME
# certificates; local_certs = Caddy's own CA. Anything else is a typo that the
# Caddyfile would paste into its global options.
trial_tls = (caddy_env.get("CADDY_TRIAL_TLS") or "").strip()
if trial_tls not in ("", "local_certs"):
    problems.append("caddy: CADDY_TRIAL_TLS must be empty or local_certs")

for name, service in sorted(config.get("services", {}).items()):
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env if "=" in item)
    for key, value in sorted(env.items()):
        if isinstance(value, str) and value.strip().startswith("#"):
            # The key only: the value may be a secret.
            problems.append(f"{name}: {key} is a comment, not a value (inline comment in .env?)")

digest_pinned = re.compile(r"@sha256:[0-9a-f]{64}$")
for name, service in sorted(config.get("services", {}).items()):
    image = service.get("image") or ""
    if not digest_pinned.search(image):
        problems.append(f"{name}: image {image!r} is not pinned by digest (@sha256:...)")
    if service.get("build"):
        problems.append(f"{name}: is built on the server; production runs published images only")

# F5: the network is dual-stack, so Docker publishes Caddy's ports for IPv6
# through ip6tables NAT (client addresses kept) instead of its userland proxy
# (every IPv6 client seen as the gateway). The proxies are pinned in both
# families, and the API and workers trust exactly those addresses and know the
# network's subnets.
import ipaddress

network = (config.get("networks") or {}).get("default") or {}
subnets = {}
if network.get("enable_ipv6") is not True:
    problems.append("network default: enable_ipv6 is not true (IPv6 clients would arrive as the gateway)")
for entry in (network.get("ipam") or {}).get("config") or []:
    try:
        subnet = ipaddress.ip_network(entry.get("subnet") or "", strict=True)
    except ValueError:
        problems.append(f"network default: invalid subnet {entry.get('subnet')!r}")
        continue
    if subnet.version in subnets:
        problems.append(f"network default: more than one IPv{subnet.version} subnet")
    subnets[subnet.version] = subnet
for version in (4, 6):
    if version not in subnets:
        problems.append(f"network default: no IPv{version} subnet")

pinned = []
for name in ("web", "caddy"):
    entry = ((config.get("services", {}).get(name, {}).get("networks") or {}).get("default")) or {}
    for version, key in ((4, "ipv4_address"), (6, "ipv6_address")):
        value = entry.get(key)
        if not value:
            problems.append(f"{name}: no pinned IPv{version} address on the default network")
            continue
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            problems.append(f"{name}: invalid IPv{version} address {value!r}")
            continue
        if address.version != version:
            problems.append(f"{name}: {key} {value} is not an IPv{version} address")
            continue
        if version in subnets and address not in subnets[version]:
            problems.append(f"{name}: IPv{version} address {value} is outside {subnets[version]}")
        pinned.append(ipaddress.ip_network(f"{value}/{address.max_prefixlen}"))

expected_trusted = sorted(map(str, pinned))
expected_internal = sorted(str(s) for s in subnets.values())


def cidr_set(raw):
    try:
        return sorted(str(ipaddress.ip_network(v.strip(), strict=True)) for v in raw.split(",") if v.strip())
    except ValueError:
        return None


for name in ("api", "worker-realtime", "worker-batch", "worker-ml"):
    env = config.get("services", {}).get(name, {}).get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env if "=" in item)
    for key, expected in (
        ("TRUSTED_PROXY_CIDRS", expected_trusted),
        ("INTERNAL_NETWORK_CIDRS", expected_internal),
    ):
        actual = cidr_set(env.get(key) or "")
        if actual != expected:
            problems.append(f"{name}: {key} is {env.get(key)!r}, expected {','.join(expected)}")

# Redis holds the pickled ARQ jobs, quotas and rate limits: a password on the
# server and in every client's REDIS_URL (rollback-safe: every image reads the
# URL), noeviction under a maxmemory (every one of those keys has a TTL), AOF.
# Messages name keys and options, never the password.
import shlex
from urllib.parse import unquote, urlsplit


def env_of(name):
    env = config.get("services", {}).get(name, {}).get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env if "=" in item)
    return env


redis_service = config.get("services", {}).get("redis") or {}
redis_password = env_of("redis").get("REDIS_PASSWORD") or ""
if not redis_password:
    problems.append("redis: REDIS_PASSWORD is empty (requirepass is off)")
command = redis_service.get("command") or []
if isinstance(command, str):
    command = [command]
tokens = [token for part in command for token in shlex.split(part.replace("$$", "$"))]


def option(flag):
    return tokens[tokens.index(flag) + 1] if flag in tokens[:-1] else None


if "requirepass" not in " ".join(command) and "REDIS_PASSWORD" not in " ".join(command):
    problems.append("redis: the command never sets requirepass")
if option("--maxmemory-policy") != "noeviction":
    problems.append(
        f"redis: --maxmemory-policy is {option('--maxmemory-policy')!r}, expected 'noeviction'"
    )
if option("--maxmemory") in (None, "0"):
    problems.append("redis: no --maxmemory (Redis would grow until its container is killed)")
if option("--appendonly") != "yes":
    problems.append(f"redis: --appendonly is {option('--appendonly')!r}, expected 'yes'")
health = " ".join((redis_service.get("healthcheck") or {}).get("test") or [])
if "REDISCLI_AUTH" not in health:
    problems.append("redis: the healthcheck does not authenticate (REDISCLI_AUTH)")

for name in ("api", "worker-realtime", "worker-batch", "worker-ml"):
    url = urlsplit(env_of(name).get("REDIS_URL") or "")
    if (url.hostname, url.port) != ("redis", 6379):
        problems.append(f"{name}: REDIS_URL points at {url.hostname}:{url.port}, expected redis:6379")
    password = unquote(url.password or "")
    if not password:
        problems.append(f"{name}: REDIS_URL carries no password")
    elif redis_password and password != redis_password:
        problems.append(f"{name}: REDIS_URL password differs from redis's REDIS_PASSWORD")
    own = env_of(name).get("REDIS_PASSWORD") or ""
    if own and password and own != password:
        problems.append(f"{name}: REDIS_PASSWORD differs from the password in REDIS_URL")

if problems:
    print("Production Compose config is unsafe or miswired:", file=sys.stderr)
    for line in problems:
        print(f"  {line}", file=sys.stderr)
    sys.exit(1)
if trial_tls:
    print(
        "caddy: CLOSED TRIAL - certificates from Caddy's own CA (CADDY_TRIAL_TLS=local_certs); "
        "unset it, open 80/443 and restart caddy to open the site (docs/DEPLOY_VPS.md)."
    )
else:
    print("caddy: public certificates (ACME).")
print(
    "OK: only caddy 80/tcp and 443/tcp are published; web reaches the API at "
    "http://api:8000; caddy gets PUBLIC_DOMAIN; no environment value is a comment; "
    "every image is pinned by digest and none is built on the server; the network is "
    "dual-stack, web and caddy are pinned in both families, and the API and workers "
    "trust exactly those addresses; Redis requires a password that the API and every "
    "worker send, never evicts, and keeps an AOF."
)
PY
