# shellcheck shell=bash
# Sourced by deploy.sh and rollback.sh (F5).
#
# check_project_network RERUN_COMMAND
#
# Compose cannot change an existing network: when the network in the Compose
# files differs from the one on the host (e.g. the dual-stack network of F5
# over the IPv4-only network of an earlier release), `compose up` fails. In
# deploy.sh that would happen mid-deploy and start an automatic rollback that
# fails the same way. So both scripts call this before touching anything; it
# returns 1 with the one-time procedure when the existing network differs in
# IPv6 or in its subnets and gateways, and 0 when it matches or does not exist
# yet (the first deploy on an empty server: Compose creates it).
#
# Needs the caller's `compose` function and python3 (PYTHON overrides it).
check_project_network() {
  local rerun="$1" python_bin="${PYTHON:-python3}" rendered name names existing report
  if ! "$python_bin" -c 'import ipaddress, json' >/dev/null 2>&1; then
    echo "The network check needs python3 ('$python_bin' does not run; set PYTHON to an interpreter); nothing was changed." >&2
    return 1
  fi
  if ! rendered="$(compose config --format json)"; then
    echo "Could not render the Compose configuration; nothing was changed." >&2
    return 1
  fi
  name="$(BP_RENDERED="$rendered" "$python_bin" -c '
import json, os
print(json.loads(os.environ["BP_RENDERED"])["networks"]["default"]["name"])
')" || {
    echo "The Compose configuration has no default network; nothing was changed." >&2
    return 1
  }
  if ! names="$(docker network ls --format '{{.Name}}')"; then
    echo "Cannot list Docker networks (is the Docker daemon running?); nothing was changed." >&2
    return 1
  fi
  if ! grep -qxF "$name" <<<"$names"; then
    return 0
  fi
  if ! existing="$(docker network inspect "$name" --format '{{json .}}')"; then
    echo "Cannot inspect the Docker network $name; nothing was changed." >&2
    return 1
  fi
  report="$(BP_RENDERED="$rendered" BP_EXISTING="$existing" "$python_bin" -c '
import ipaddress, json, os

def norm(subnet, gateway):
    net = ipaddress.ip_network(subnet, strict=False)
    gw = ipaddress.ip_address(gateway).compressed if gateway else None
    return net.compressed, gw

configured = json.loads(os.environ["BP_RENDERED"])["networks"]["default"]
existing = json.loads(os.environ["BP_EXISTING"])
want_v6 = configured.get("enable_ipv6") is True
have_v6 = existing.get("EnableIPv6") is True
want = dict(norm(c["subnet"], c.get("gateway")) for c in (configured.get("ipam") or {}).get("config") or [])
have = dict(norm(c["Subnet"], c.get("Gateway")) for c in (existing.get("IPAM") or {}).get("Config") or [])
# A gateway the Compose files leave to Docker matches whatever Docker chose.
differs = (
    want_v6 != have_v6
    or set(want) != set(have)
    or any(gateway and have[subnet] != gateway for subnet, gateway in want.items())
)

def describe(v6, subnets):
    parts = [f"{s} (gateway {g})" if g else s for s, g in sorted(subnets.items())]
    return ("IPv6 on; " if v6 else "IPv6 off; ") + ", ".join(parts)

if differs:
    print(f"  existing:   {describe(have_v6, have)}")
    print(f"  configured: {describe(want_v6, want)}")
')" || {
    echo "Cannot compare the Docker network $name with the Compose configuration; nothing was changed." >&2
    return 1
  }
  if [[ -z "$report" ]]; then
    return 0
  fi
  cat >&2 <<EOF
The Docker network $name does not match the Compose configuration:
$report
Compose cannot change a network in place, so nothing was changed. One-time
procedure (the site is down between the two steps; volumes and data are kept):
  1. scripts/prod-compose.sh down        # never -v: that deletes the database volumes
  2. $rerun
EOF
  return 1
}
