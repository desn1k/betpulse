#!/usr/bin/env bash
# Deploy a release by digest: IMAGE_TAG=vX.Y.Z RELEASE_DIGESTS=release-vX.Y.Z.digests
#
# Exit codes (HANDOFF section 9i):
#   0  deployed, healthy and ready;
#   1  refused before anything changed (no .env, bad IMAGE_TAG, bad or missing digests file);
#   2  deployment failed, the previous release was restored and is healthy and ready;
#   3  deployment failed and the automatic rollback failed too: the site may be down;
#   4  deployment failed, no automatic rollback (first deploy here, or no stored digests
#      for the previous release).
# 2, 3 and 4 also say whether migrations ran: the schema is never rolled back.
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$root_dir/.env"
state_dir="$root_dir/.release"
last_successful_tag_file="$state_dir/last-successful-image-tag"
health_attempts="${DEPLOY_HEALTHCHECK_ATTEMPTS:-30}"
# Per-request timeout of the BFF readiness probe (busybox wget -T), so a stalled
# request cannot hold the retry loop: the whole check is bounded by
# attempts x (timeout + 2 s).
probe_timeout="${DEPLOY_READY_TIMEOUT_SECONDS:-5}"
# Application services built from release images. Keep in sync with
# infra/docker-compose*.yml (one ARQ worker per queue, app/workers/queues.py).
app_services=(api worker-realtime worker-batch worker-ml web mlflow)

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy .env.example and configure production secrets first." >&2
  exit 1
fi

image_tag="${IMAGE_TAG:-}"
if [[ -z "$image_tag" || "$image_tag" == "latest" || ! "$image_tag" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]]; then
  echo "Set IMAGE_TAG to an immutable published release tag (for example v1.2.3)." >&2
  exit 1
fi

# Every app image is deployed by the digest release.yml published, never by
# its tag alone (a GHCR tag can be re-pushed): the release-<tag>.digests asset
# of the GitHub Release, or the copy kept from an earlier deploy of this tag.
# shellcheck source=scripts/release-digests.sh
. "$root_dir/scripts/release-digests.sh"
digests_file="${RELEASE_DIGESTS:-$state_dir/$image_tag.digests}"
if [[ ! -f "$digests_file" ]]; then
  echo "Set RELEASE_DIGESTS to release-$image_tag.digests, the asset of the GitHub Release $image_tag (HANDOFF section 9i)." >&2
  exit 1
fi
load_release_digests "$digests_file" "$image_tag" || exit 1

compose() {
  IMAGE_TAG="$image_tag" docker compose --env-file "$env_file" \
    -f "$root_dir/infra/docker-compose.yml" \
    -f "$root_dir/infra/docker-compose.prod.yml" "$@"
}

wait_for_service() {
  local service="$1"
  local container status attempt
  container="$(compose ps -q "$service")"
  if [[ -z "$container" ]]; then
    echo "Service $service has no container." >&2
    return 1
  fi

  for ((attempt = 1; attempt <= health_attempts; attempt++)); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")"
    if [[ "$status" == "healthy" || "$status" == "running" ]]; then
      return 0
    fi
    sleep 2
  done

  echo "Service $service did not become healthy." >&2
  compose logs --tail=100 "$service" >&2 || true
  return 1
}

# GET /api/ready from inside the web container: the BFF relays the backend's
# readiness, so this proves the web -> API hop over the internal network (no
# DNS or TLS involved). /api/health only proves the web process is up.
# Returns 0 ready, 1 not (yet) ready, 2 the image has no /api/ready (older
# than the check).
bff_ready() {
  local out
  if out="$(compose exec -T web wget -q -T "$probe_timeout" -O /dev/null http://127.0.0.1:3000/api/ready 2>&1)"; then
    return 0
  fi
  if [[ "$out" == *" 404"* ]]; then
    return 2
  fi
  return 1
}

wait_for_bff_ready() {
  local attempt status
  for ((attempt = 1; attempt <= health_attempts; attempt++)); do
    status=0
    bff_ready || status=$?
    if [[ "$status" -eq 0 ]]; then
      return 0
    fi
    if [[ "$status" -eq 2 ]]; then
      echo "Image $image_tag has no /api/ready; deploy a release that includes the BFF readiness check." >&2
      return 1
    fi
    sleep 2
  done
  echo "The web container cannot reach the API: GET /api/ready inside the Docker network failed after $health_attempts attempts. Check that web's API_BASE_URL is http://api:8000 and that the api service is healthy." >&2
  compose logs --tail=50 web api >&2 || true
  return 1
}

# TimescaleDB: a new image never updates the extension of an existing database
# (the init scripts run only on an empty volume), and ALTER EXTENSION must be
# the first command of its own `psql -X` session, so it cannot be an Alembic
# revision (that fails: "cannot be updated after the old version has already
# been loaded"). On the first launch the extension does not exist yet and
# migration 0003 creates it at the image's version, so nothing is done.
# Every connectable database is checked, so the one DATABASE_URL (Alembic)
# targets is covered whatever its name; today only that database has it.
psql_in() {
  # psql -X in a fresh session on database $1, SQL on stdin.
  # shellcheck disable=SC2016  # $POSTGRES_USER / $BP_DB expand in the container's shell
  compose exec -T -e BP_DB="$1" postgres \
    sh -c 'psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$BP_DB"'
}

update_timescale_extension() {
  local databases db installed
  databases="$(psql_in postgres <<<"SELECT datname FROM pg_database WHERE datallowconn ORDER BY datname;")"
  while IFS= read -r db; do
    [[ -n "$db" ]] || continue
    installed="$(psql_in "$db" <<<"SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';")"
    [[ -n "$installed" ]] || continue
    echo "TimescaleDB $installed in database $db; running ALTER EXTENSION timescaledb UPDATE."
    psql_in "$db" <<<"ALTER EXTENSION timescaledb UPDATE;"
  done <<<"$databases"
}

previous_tag=""
if [[ -f "$last_successful_tag_file" ]]; then
  previous_tag="$(<"$last_successful_tag_file")"
fi

migrations_ran=false
schema_note() {
  if [[ "$migrations_ran" == true ]]; then
    echo "Note: the database schema is NOT rolled back: the migrations of $image_tag already ran (HANDOFF ER-H-08)." >&2
  else
    echo "Note: no migration ran, so the database schema is unchanged." >&2
  fi
}

# Any failing step lands here. The previous release is restored by
# scripts/rollback.sh itself (pull and up by its stored digests, then wait for
# every service and /api/ready), so a manual and an automatic rollback are the
# same code path and the outcome is known: exit 2 restored, exit 3 not.
rollback_on_failure() {
  trap - ERR
  if [[ -z "$previous_tag" || "$previous_tag" == "$image_tag" ]]; then
    echo "Deployment of $image_tag failed; there is no earlier release to roll back to." >&2
    schema_note
    exit 4
  fi
  if [[ ! -f "$state_dir/$previous_tag.digests" ]]; then
    echo "Deployment of $image_tag failed. No stored digests for $previous_tag: not rolling back automatically. Run scripts/rollback.sh with IMAGE_TAG=$previous_tag and RELEASE_DIGESTS=release-$previous_tag.digests." >&2
    schema_note
    exit 4
  fi
  echo "Deployment of $image_tag failed; rolling back to $previous_tag (scripts/rollback.sh)." >&2
  if IMAGE_TAG="$previous_tag" RELEASE_DIGESTS="$state_dir/$previous_tag.digests" \
    bash "$root_dir/scripts/rollback.sh" </dev/null; then
    echo "Deployment of $image_tag failed; rolled back to $previous_tag, which is healthy and ready." >&2
    schema_note
    exit 2
  fi
  echo "AUTOMATIC ROLLBACK TO $previous_tag FAILED: the site may be down. Check scripts/prod-compose.sh ps and the logs, then run IMAGE_TAG=<release> scripts/rollback.sh by hand." >&2
  schema_note
  exit 3
}
trap rollback_on_failure ERR

compose pull "${app_services[@]}"
compose up -d postgres redis
wait_for_service postgres
wait_for_service redis

update_timescale_extension
migrations_ran=true # set first: a half-applied upgrade has changed the schema too
compose run --rm api alembic upgrade head
compose up -d --remove-orphans
for service in "${app_services[@]}" caddy; do
  wait_for_service "$service"
done
# Failing here triggers rollback_on_failure like any other step.
wait_for_bff_ready

mkdir -p "$state_dir"
umask 077
if [[ "$digests_file" != "$state_dir/$image_tag.digests" ]]; then
  cp "$digests_file" "$state_dir/$image_tag.digests"
fi
printf '%s\n' "$image_tag" > "$last_successful_tag_file"
echo "Deployment of $image_tag completed successfully."
