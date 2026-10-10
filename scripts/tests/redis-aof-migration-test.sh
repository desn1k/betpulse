#!/usr/bin/env bash
# Behaviour test, on a real Docker daemon, of the one-time move of an existing
# Redis to AOF (HANDOFF §9i, "Redis persistence"). A Redis as earlier releases
# ran it (RDB snapshots only, no password) holds keys; this release's config
# starts Redis with AOF on, which on its own would start it EMPTY: Redis 7.4
# creates a new, empty AOF and ignores dump.rdb. Scenarios:
#   A. running old Redis: deploy's guard refuses; scripts/redis-enable-aof.sh
#      takes a BGSAVE, copies the dump out of the container and turns AOF on
#      live; the guard passes; the new config starts with every key and TTL.
#   B. stopped old Redis: the guard refuses; the script starts a temporary
#      Redis on the volume, converts it and stops it; the new config starts
#      with every key.
#   C. control: without the script the new config starts empty.
#   D. backup and restore: scripts/redis-backup.sh copies the dump out;
#      scripts/redis-restore.sh puts exactly that copy back (checksum checked,
#      explicit --replace-current-data), with AOF on and the password required.
# Each scenario is its own Compose project on its own subnets, never the
# stack's, and is removed afterwards. Needs Docker Compose and REDIS_PASSWORD.
set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_example="${1:-$repo_dir/.env.example}"
if [[ -z "${REDIS_PASSWORD:-}" ]]; then
  echo "Set REDIS_PASSWORD (letters and digits) to run this test." >&2
  exit 1
fi
work="$(mktemp -d)"
current_project=""
remove_project() {
  local project="$1"
  [[ -n "$project" ]] || return 0
  docker ps -aq --filter "label=com.docker.compose.project=$project" | xargs -r docker rm -f >/dev/null 2>&1 || true
  docker volume ls -q --filter "label=com.docker.compose.project=$project" | xargs -r docker volume rm >/dev/null 2>&1 || true
  docker network ls -q --filter "label=com.docker.compose.project=$project" | xargs -r docker network rm >/dev/null 2>&1 || true
}
cleanup() {
  remove_project "$current_project"
  rm -rf "$work"
}
trap cleanup EXIT

# Rendering the prod overlay needs these; nothing but redis is started.
export IMAGE_TAG=v0.0.0-ci PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-betpulse.example.test}"
export BETPULSE_NETWORK_SUBNET=172.29.198.0/24 BETPULSE_NETWORK_GATEWAY=172.29.198.1
export BETPULSE_NETWORK_SUBNET6=fd42:b7e1:5a29:198::/64 BETPULSE_NETWORK_GATEWAY6=fd42:b7e1:5a29:198::1
export BETPULSE_WEB_IP=172.29.198.10 BETPULSE_CADDY_IP=172.29.198.11
export BETPULSE_WEB_IP6=fd42:b7e1:5a29:198::10 BETPULSE_CADDY_IP6=fd42:b7e1:5a29:198::11

# Redis as earlier releases ran it.
cat >"$work/old-redis.yml" <<'YAML'
services:
  redis:
    command: !override ["redis-server", "--save", "60", "1", "--loglevel", "warning"]
    environment: !reset {}
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
YAML

failures=0
fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}
ok() { echo "ok   $*"; }

# start_scenario NAME: a server checkout (scripts, infra, .env, and .release/
# naming a deployed release, which prod-compose.sh needs) under its own project.
start_scenario() {
  local digit
  root="$work/$1"
  current_project="bp-aof-$1-$$"
  export COMPOSE_PROJECT_NAME="$current_project"
  mkdir -p "$root/.release"
  cp -r "$repo_dir/scripts" "$repo_dir/infra" "$root/"
  {
    cat "$env_example"
    echo "REDIS_PASSWORD=$REDIS_PASSWORD"
    echo "POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-ci-postgres-password}"
  } >"$root/.env"
  echo "v0.0.0-ci" >"$root/.release/last-successful-image-tag"
  {
    echo "RELEASE_VERSION=v0.0.0-ci"
    for digit in API:1 WEB:2 MLFLOW:3; do
      printf '%s_IMAGE_DIGEST=sha256:%s\n' "${digit%%:*}" "$(printf '%064d' 0 | tr 0 "${digit#*:}")"
    done
  } >"$root/.release/v0.0.0-ci.digests"
}

end_scenario() {
  remove_project "$current_project"
  current_project=""
}

# old_compose ARGS: this checkout's Compose files with the old Redis on top.
old_compose() {
  (
    # shellcheck source=scripts/release-digests.sh
    . "$root/scripts/release-digests.sh"
    load_release_digests "$root/.release/v0.0.0-ci.digests" v0.0.0-ci
    docker compose --env-file "$root/.env" -f "$root/infra/docker-compose.yml" \
      -f "$root/infra/docker-compose.prod.yml" -f "$work/old-redis.yml" "$@"
  )
}

# wait_for_ping CONTAINER PASSWORD
wait_for_ping() {
  local _
  for _ in $(seq 1 30); do
    if [[ "$(docker exec -e REDISCLI_AUTH="$2" "$1" redis-cli --no-auth-warning ping 2>/dev/null)" == "PONG" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# start_old_redis: the old Redis with five keys, three of them with a TTL.
start_old_redis() {
  local container
  old_compose up -d --no-deps redis >/dev/null 2>&1
  container="$(old_compose ps -q redis)"
  wait_for_ping "$container" ""
  docker exec "$container" sh -c '
    redis-cli set limits:guest:1 5 EX 3600 >/dev/null
    redis-cli incr rl:refresh:ip:x >/dev/null && redis-cli expire rl:refresh:ip:x 600 >/dev/null
    redis-cli zadd arq:queue:realtime 1 job1 >/dev/null
    redis-cli set arq:job:job1 payload PX 86400000 >/dev/null
    redis-cli sadd limits:seen:u1 m1 m2 >/dev/null'
}

# guard: deploy.sh's check, as deploy.sh runs it.
guard() {
  (
    compose() { "$root/scripts/prod-compose.sh" "$@"; }
    # shellcheck source=scripts/redis-persistence.sh
    . "$root/scripts/redis-persistence.sh"
    check_redis_persistence "the deploy command"
  )
}

# new_redis_keys: start this release's Redis; print its key count.
new_redis_keys() {
  local container
  if ! "$root/scripts/prod-compose.sh" up -d --no-deps redis >"$work/up.log" 2>&1; then
    echo "not started: $(tail -n 3 "$work/up.log")"
    return 0
  fi
  container="$("$root/scripts/prod-compose.sh" ps -q redis)"
  if ! wait_for_ping "$container" "$REDIS_PASSWORD"; then
    echo "unreachable"
    return 0
  fi
  docker exec -e REDISCLI_AUTH="$REDIS_PASSWORD" "$container" redis-cli --no-auth-warning dbsize
}

job_ttl() {
  local container
  container="$("$root/scripts/prod-compose.sh" ps -q redis)"
  docker exec -e REDISCLI_AUTH="$REDIS_PASSWORD" "$container" redis-cli --no-auth-warning pttl arq:job:job1 || echo 0
}

# --- A. running old Redis ------------------------------------------------------
start_scenario running
start_old_redis
if out="$(guard 2>&1)"; then
  fail "running: the guard let a deploy start Redis with AOF over an RDB-only dataset"
elif [[ "$out" != *"scripts/redis-enable-aof.sh"* || "$out" != *"the deploy command"* ]]; then
  fail "running: the guard did not print the procedure: $out"
else
  ok "running: the guard refuses and prints the procedure"
fi
if out="$(bash "$root/scripts/redis-enable-aof.sh" 2>&1)"; then
  ok "running: redis-enable-aof.sh succeeded"
else
  fail "running: redis-enable-aof.sh failed: $out"
fi
backup="$(find "$root/.release/redis" -name 'dump-*.rdb' -size +0 2>/dev/null | head -n 1 || true)"
if [[ -n "$backup" && -f "$backup.sha256" ]]; then ok "running: the dump was copied out with its checksum"; else fail "running: no dump copy in .release/redis"; fi
[[ "$out" == *"5 keys"* ]] || fail "running: the key count is not reported: $out"
if guard >/dev/null 2>&1; then ok "running: the guard passes once AOF is on"; else fail "running: the guard still refuses"; fi
keys="$(new_redis_keys)"
if [[ "$keys" == "5" ]]; then ok "running: the new config starts with all 5 keys"; else fail "running: the new config has $keys keys, expected 5"; fi
ttl="$(job_ttl)"
if ((ttl > 86000000)); then ok "running: TTLs are kept"; else fail "running: arq:job:job1 pttl is $ttl"; fi
end_scenario

# --- B. stopped old Redis ------------------------------------------------------
start_scenario stopped
start_old_redis
old_compose stop redis >/dev/null 2>&1 # SIGTERM: Redis saves dump.rdb
if out="$(guard 2>&1)"; then
  fail "stopped: the guard let a deploy start Redis with AOF over an RDB-only volume"
elif [[ "$out" != *"scripts/redis-enable-aof.sh"* ]]; then
  fail "stopped: the guard did not print the procedure: $out"
else
  ok "stopped: the guard refuses and prints the procedure"
fi
if out="$(bash "$root/scripts/redis-enable-aof.sh" 2>&1)"; then
  ok "stopped: redis-enable-aof.sh succeeded"
else
  fail "stopped: redis-enable-aof.sh failed: $out"
fi
if [[ -n "$(docker ps -q --filter "label=com.docker.compose.project=$current_project")" ]]; then
  fail "stopped: a temporary Redis is still running"
fi
if guard >/dev/null 2>&1; then ok "stopped: the guard passes once AOF is on"; else fail "stopped: the guard still refuses"; fi
keys="$(new_redis_keys)"
if [[ "$keys" == "5" ]]; then ok "stopped: the new config starts with all 5 keys"; else fail "stopped: the new config has $keys keys, expected 5"; fi
end_scenario

# --- C. control: no conversion -------------------------------------------------
start_scenario control
start_old_redis
keys="$(new_redis_keys)"
if [[ "$keys" == "0" ]]; then
  ok "control: without the conversion the new config starts empty (why the guard exists)"
else
  fail "control: expected an empty Redis, got $keys keys"
fi
end_scenario

# --- D. backup and restore on this release's Redis -----------------------------
# redis-backup.sh takes a copy; later writes and a lost key; redis-restore.sh
# brings back exactly the copy, with AOF on and the password required.
start_scenario restore
start_old_redis
bash "$root/scripts/redis-enable-aof.sh" >/dev/null 2>&1 || fail "restore: setup conversion failed"
keys="$(new_redis_keys)"
[[ "$keys" == "5" ]] || fail "restore: setup has $keys keys, expected 5"
if out="$(bash "$root/scripts/redis-backup.sh" 2>&1)"; then
  ok "restore: redis-backup.sh succeeded"
else
  fail "restore: redis-backup.sh failed: $out"
fi
copy="$(sed -n 's/^Copied the dump to \(.*\.rdb\) (.*/\1/p' <<<"$out" | tail -n 1)"
if [[ -s "$copy" && -f "$copy.sha256" ]]; then ok "restore: the copy and its checksum exist"; else fail "restore: no copy reported: $out"; fi
container="$("$root/scripts/prod-compose.sh" ps -q redis)"
docker exec -e REDISCLI_AUTH="$REDIS_PASSWORD" "$container" sh -c \
  'redis-cli --no-auth-warning set after:backup x >/dev/null && redis-cli --no-auth-warning del limits:seen:u1 >/dev/null'
if out="$(bash "$root/scripts/redis-restore.sh" "$copy" 2>&1)"; then
  fail "restore: ran without --replace-current-data"
elif [[ "$out" != *"--replace-current-data"* ]]; then
  fail "restore: the refusal does not name --replace-current-data: $out"
else
  ok "restore: refuses without --replace-current-data"
fi
if [[ -s "$copy" ]]; then
  cp "$copy" "$work/tampered.rdb"
  printf 'x' >>"$work/tampered.rdb"
  sed "s#$(basename "$copy")#tampered.rdb#" "$copy.sha256" >"$work/tampered.rdb.sha256"
fi
if out="$(bash "$root/scripts/redis-restore.sh" "$work/tampered.rdb" --replace-current-data 2>&1)"; then
  fail "restore: accepted a copy whose checksum does not match"
else
  ok "restore: refuses a copy whose checksum does not match"
fi
if out="$(timeout 300 bash -x "$root/scripts/redis-restore.sh" "$copy" --replace-current-data 2>&1)"; then
  ok "restore: redis-restore.sh succeeded"
else
  fail "restore: redis-restore.sh failed: $out"
fi
container="$("$root/scripts/prod-compose.sh" ps -q redis)"
if wait_for_ping "$container" "$REDIS_PASSWORD"; then
  rcli() { docker exec -e REDISCLI_AUTH="$REDIS_PASSWORD" "$container" redis-cli --no-auth-warning "$@"; }
  if [[ "$(rcli dbsize)" == "5" ]]; then ok "restore: 5 keys, as in the copy"; else fail "restore: $(rcli dbsize) keys, expected 5"; fi
  if [[ "$(rcli exists limits:seen:u1)" == "1" ]]; then ok "restore: the lost key is back"; else fail "restore: limits:seen:u1 missing"; fi
  if [[ "$(rcli exists after:backup)" == "0" ]]; then ok "restore: a write after the copy is gone"; else fail "restore: after:backup still there"; fi
  if (($(rcli pttl arq:job:job1) > 86000000)); then ok "restore: TTLs are kept"; else fail "restore: arq:job:job1 lost its TTL"; fi
  if [[ "$(rcli config get appendonly | tail -n 1)" == "yes" ]]; then ok "restore: AOF is on"; else fail "restore: AOF is off"; fi
  if [[ "$(docker exec "$container" redis-cli ping 2>&1)" == *NOAUTH* ]]; then ok "restore: the password is required"; else fail "restore: anonymous ping answered"; fi
else
  fail "restore: Redis did not come back"
fi
end_scenario

if ((failures > 0)); then
  echo "$failures Redis AOF migration check(s) failed." >&2
  exit 1
fi
echo "OK: an RDB-only Redis is refused by the guard and moved to AOF with every key kept, running or stopped; a copy restores exactly."
