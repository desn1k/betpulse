#!/usr/bin/env bash
# Read-only diagnosis of client identity on the server (F5). Run once on the
# VPS after a deploy, from the repository directory:
#
#   scripts/diagnose-client-ip.sh [WATCH_SECONDS]      (default 120; 0 = facts only)
#
# Part 1 prints the facts that decide how a client reaches Caddy: the Docker
# version and daemon settings, the project network (IPv6, subnets, the proxies'
# addresses), who listens on 80/443 (docker-proxy or not), the NAT rules for
# 80/443, the host's IPv6 setup and the domain's A/AAAA records.
#
# Part 2 watches for WATCH_SECONDS while you open the site from an IPv4 client
# and from an IPv6 client, both signed out (a guest is counted by address, a
# signed-in user by id), and a match page on each. It prints every address
# Caddy sees on 80/443 and every new guest quota key the API writes in Redis,
# each marked "real client" or "OUR NETWORK (collapse)". A collapse address is
# the bridge gateway: every client arriving that way shares one identity.
#
# It changes nothing: docker exec runs netstat in caddy and a SCAN in Redis.
# Commands that need root (ss -p, iptables) use `sudo -n` and are skipped
# without it. Run it from a client-free terminal: a request this server sends
# to itself (curl https://localhost) also arrives as the gateway.
set -Euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
watch_seconds="${1:-120}"
python_bin="${PYTHON:-python3}"
network="${BETPULSE_NETWORK_NAME:-betpulse_default}"

section() { printf '\n== %s ==\n' "$1"; }
as_root() {
  if sudo -n true 2>/dev/null; then
    sudo -n "$@"
  else
    echo "(skipped: needs sudo) $*"
  fi
}

section "Docker"
docker version --format 'Engine {{.Server.Version}}, API {{.Server.APIVersion}}' || true
docker info --format 'OS: {{.OperatingSystem}}' || true
if [[ -f /etc/docker/daemon.json ]]; then
  echo "/etc/docker/daemon.json:"
  sed 's/^/  /' /etc/docker/daemon.json
else
  echo "/etc/docker/daemon.json: absent (defaults: ip6tables on since Engine 27, userland-proxy on)"
fi
echo "docker-proxy processes listening for 80/443 (userland proxy in use):"
pgrep -a docker-proxy | grep -E -- '-host-port (80|443)( |$)' | sed 's/^/  /' || echo "  none"

section "Project network $network"
if ! docker network inspect "$network" >/dev/null 2>&1; then
  echo "No network $network: is the stack deployed here? (BETPULSE_NETWORK_NAME overrides the name)"
  exit 1
fi
network_json="$(docker network inspect "$network" --format '{{json .}}')"
BP_NETWORK="$network_json" "$python_bin" -c '
import json, os
n = json.loads(os.environ["BP_NETWORK"])
print("EnableIPv6:", n.get("EnableIPv6"))
for c in (n.get("IPAM") or {}).get("Config") or []:
    print("subnet", c.get("Subnet"), " gateway", c.get("Gateway"))
for c in sorted((n.get("Containers") or {}).values(), key=lambda c: c.get("Name", "")):
    if any(s in c.get("Name", "") for s in ("caddy", "web")):
        print(c.get("Name") + ":", c.get("IPv4Address"), c.get("IPv6Address") or "(no IPv6)")
'

section "Listeners on 80/443"
as_root ss -Htlnp '( sport = :80 or sport = :443 )'

section "NAT rules for 80/443 (DNAT keeps the client's address)"
if sudo -n true 2>/dev/null; then
  echo "IPv4:"
  sudo -n iptables -t nat -S DOCKER 2>/dev/null | grep -E -- '--dport (80|443) ' | sed 's/^/  /' ||
    echo "  none"
  echo "IPv6:"
  sudo -n ip6tables -t nat -S DOCKER 2>/dev/null | grep -E -- '--dport (80|443) ' | sed 's/^/  /' ||
    echo "  none: IPv6 clients go through docker-proxy and arrive as the gateway"
else
  echo "(skipped: needs sudo) iptables / ip6tables -t nat -S DOCKER"
fi

section "Host IPv6"
ip -6 addr show scope global | sed 's/^/  /' || true
echo "default route:"
ip -6 route show default | sed 's/^/  /' || true
echo "net.ipv6.conf.all.forwarding = $(sysctl -n net.ipv6.conf.all.forwarding 2>/dev/null || echo '?')"
uplink="$(ip -6 route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") print $(i + 1)}' | head -1)"
if [[ -n "$uplink" ]]; then
  # With forwarding on, the kernel ignores router advertisements unless
  # accept_ra is 2: an RA-configured address or route can lapse.
  echo "net.ipv6.conf.$uplink.accept_ra = $(sysctl -n "net.ipv6.conf.$uplink.accept_ra" 2>/dev/null || echo '?') (2 needed with forwarding on, if the IPv6 route comes from router advertisements)"
fi

section "DNS"
domain="$(sed -n 's/^PUBLIC_DOMAIN=//p' "$root_dir/.env" 2>/dev/null | tail -1)"
if [[ -n "$domain" ]]; then
  echo "A    $domain: $(getent ahostsv4 "$domain" | awk '{print $1}' | sort -u | tr '\n' ' ')"
  echo "AAAA $domain: $(getent ahostsv6 "$domain" | awk '{print $1}' | grep -v '^::ffff:' | sort -u | tr '\n' ' ')"
else
  echo "PUBLIC_DOMAIN not found in .env"
fi

if [[ "$watch_seconds" == "0" ]]; then
  exit 0
fi

caddy="$("$root_dir/scripts/prod-compose.sh" ps -q caddy 2>/dev/null)"
redis="$("$root_dir/scripts/prod-compose.sh" ps -q redis 2>/dev/null)"
if [[ -z "$caddy" || -z "$redis" ]]; then
  echo "caddy or redis is not running; nothing to watch."
  exit 1
fi

guest_keys() {
  # The container's own REDIS_PASSWORD: it never leaves the container.
  # shellcheck disable=SC2016 # expanded by the container's shell
  docker exec "$redis" sh -c \
    'if [ -n "${REDIS_PASSWORD:-}" ]; then export REDISCLI_AUTH="$REDIS_PASSWORD"; fi; redis-cli --no-auth-warning --scan --pattern "limits:*"' \
    2>/dev/null | sort -u
}
before="$(guest_keys)"

section "Watching for $watch_seconds s"
echo "Now open https://${domain:-<domain>}/ and a match page, signed out, from an IPv4 client"
echo "and from an IPv6 client (e.g. a phone on mobile data; test-ipv6.com shows which you have)."
peers=""
deadline=$((SECONDS + watch_seconds))
while ((SECONDS < deadline)); do
  peers+="$(docker exec "$caddy" netstat -tn 2>/dev/null |
    awk '$1 ~ /^tcp/ && $4 ~ /:(80|443)$/ {print $5}')"$'\n'
  sleep 1
done
after="$(guest_keys)"

section "Result"
BP_NETWORK="$network_json" BP_PEERS="$peers" BP_BEFORE="$before" BP_AFTER="$after" "$python_bin" -c '
import ipaddress, json, os, re

n = json.loads(os.environ["BP_NETWORK"])
ours = [ipaddress.ip_network(c["Subnet"]) for c in (n.get("IPAM") or {}).get("Config") or []]

def verdict(address):
    try:
        ip = ipaddress.ip_network(address, strict=False).network_address
    except ValueError:
        return "?"
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in net for net in ours):
        return "OUR NETWORK (collapse: every client arriving so shares this identity)"
    return f"real IPv{ip.version} client"

peers = set()
for line in os.environ["BP_PEERS"].splitlines():
    host = line.strip().rsplit(":", 1)[0].strip("[]")
    if host:
        peers.add(host)
print("Addresses Caddy saw on 80/443:")
for p in sorted(peers) or []:
    print(f"  {p:45} {verdict(p)}")
if not peers:
    print("  none (no connection was open while it was polled; keep a page loading or retry)")

before = set(os.environ["BP_BEFORE"].split())
new = sorted(set(os.environ["BP_AFTER"].split()) - before)
print("New guest quota keys in Redis (the identity the API counted):")
shown = 0
for key in new:
    m = re.match(r"^limits:(?:seen:)?(.+):\d{4}-\d{2}-\d{2}$", key)
    if not m or re.fullmatch(r"[0-9a-f-]{36}", m.group(1)):
        continue  # a signed-in user (by id) or another counter
    shown += 1
    print(f"  {key:60} {verdict(m.group(1))}")
if not shown:
    print("  none (open a match page while signed out)")

families = {ipaddress.ip_address(p).version for p in peers if "OUR NETWORK" not in verdict(p) and verdict(p) != "?"}
collapsed = any("OUR NETWORK" in verdict(p) for p in peers)
print()
print("IPv4 real client seen:", "yes" if 4 in families else "NO")
print("IPv6 real client seen:", "yes" if 6 in families else "NO")
print("Gateway / internal address seen:", "YES: F5 is not fixed here" if collapsed else "no")
'
