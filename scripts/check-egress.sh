#!/usr/bin/env bash
# Can this server reach every outside service BetPulse needs? Run it on a new
# server (or a trial VM at the candidate provider) before relying on it
# (docs/DEPLOY_VPS.md, section 2). Read-only: one HTTPS request per host and
# address family, nothing is sent but a plain GET of "/".
#
# Each row: host, IPv4 result, IPv6 result, what it is for. "ok (N)" means a
# TLS connection and an HTTP answer N; any status counts as reachable (401/404
# are normal without a key), except 403, which is marked "403?" because some
# providers answer 403 to whole regions. IPv6: "no AAAA" when the host has no
# IPv6 address (not a failure), "skipped" when this server has no IPv6 route.
#
# Exit 1 when any host is unreachable over IPv4; IPv6 failures and 403s are
# warnings. LLM_HOST overrides the LLM provider's host (the one set in
# Admin -> LLM; api.openai.com when none is set).
#
# Usage: scripts/check-egress.sh        (on the server itself, not in a container)
set -Eeuo pipefail

llm_host="${LLM_HOST:-api.openai.com}"
# host|purpose
targets=(
  "ghcr.io|release images (api, web, mlflow)"
  "pkg-containers.githubusercontent.com|GHCR image layers"
  "registry-1.docker.io|Docker Hub images (postgres, redis, caddy)"
  "github.com|clone, release digests files"
  "api.sportmonks.com|Sportmonks (fixtures, results, live)"
  "api.the-odds-api.com|The Odds API (odds)"
  "api.telegram.org|Telegram bot (pushes, ops alerts)"
  "fcm.googleapis.com|Web Push: Chrome, Edge, Android"
  "web.push.apple.com|Web Push: Safari, iOS"
  "updates.push.services.mozilla.com|Web Push: Firefox"
  "$llm_host|LLM provider (match analysis)"
  "www.football-data.co.uk|historical CSVs (bootstrap-history)"
)

# probe FAMILY HOST: "ok (status)", "403?" or "FAIL".
probe() {
  local status
  if status="$(curl "-$1" -sS -o /dev/null --connect-timeout 5 --max-time 15 \
    -w '%{http_code}' "https://$2/" 2>/dev/null)" && [[ "$status" != "000" ]]; then
    if [[ "$status" == "403" ]]; then echo "403?"; else echo "ok ($status)"; fi
  else
    echo "FAIL"
  fi
}

has_aaaa() {
  getent ahostsv6 "$1" 2>/dev/null | awk '{print $1}' | grep -v '^::ffff:' | grep -q ':'
}

v6_route=true
if [[ -z "$(ip -6 route show default 2>/dev/null)" ]]; then
  v6_route=false
fi

fail4=()
fail6=()
warnings=()
printf '%-36s %-12s %-12s %s\n' "HOST" "IPv4" "IPv6" "FOR"
for target in "${targets[@]}"; do
  host="${target%%|*}"
  purpose="${target#*|}"
  if getent ahostsv4 "$host" >/dev/null 2>&1; then
    r4="$(probe 4 "$host")"
  else
    r4="FAIL"
  fi
  if [[ "$v6_route" == false ]]; then
    r6="skipped"
  elif ! has_aaaa "$host"; then
    r6="no AAAA"
  else
    r6="$(probe 6 "$host")"
  fi
  printf '%-36s %-12s %-12s %s\n' "$host" "$r4" "$r6" "$purpose"
  [[ "$r4" == "FAIL" ]] && fail4+=("$host")
  [[ "$r6" == "FAIL" ]] && fail6+=("$host")
  if [[ "$r4" == "403?" || "$r6" == "403?" ]]; then
    warnings+=("$host answered 403: it may refuse this server's region; check from its documentation or support")
  fi
done

echo
if [[ "$v6_route" == false ]]; then
  warnings+=("this server has no IPv6 default route: IPv6 not checked (docs/DEPLOY_VPS.md, section 3)")
fi
if ((${#fail6[@]} > 0)); then
  warnings+=("IPv6 unreachable: ${fail6[*]} (the app may still use IPv4 for them)")
elif [[ "$v6_route" == true ]]; then
  echo "IPv6: every host with an AAAA record reachable."
fi
for warning in "${warnings[@]}"; do
  echo "WARN: $warning"
done
if ((${#fail4[@]} > 0)); then
  echo "IPv4 unreachable: ${fail4[*]}" >&2
  echo "Do not deploy from this server until these are reachable." >&2
  exit 1
fi
echo "IPv4: every host reachable."
