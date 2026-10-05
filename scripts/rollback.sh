#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$root_dir/.env"
state_dir="$root_dir/.release"
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
  echo "Set IMAGE_TAG to the immutable release tag to restore (for example v1.2.2)." >&2
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

  echo "Service $service did not become healthy after rollback." >&2
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

verify_bff_ready() {
  local attempt status
  for ((attempt = 1; attempt <= health_attempts; attempt++)); do
    status=0
    bff_ready || status=$?
    if [[ "$status" -eq 0 ]]; then
      return 0
    fi
    if [[ "$status" -eq 2 ]]; then
      echo "Warning: image $image_tag predates /api/ready; the web -> API check was skipped. Verify the site by hand." >&2
      return 0
    fi
    sleep 2
  done
  echo "After rollback the web container still cannot reach the API (GET /api/ready failed after $health_attempts attempts). Check web's API_BASE_URL and the api service." >&2
  compose logs --tail=50 web api >&2 || true
  return 1
}

compose pull "${app_services[@]}"
# --remove-orphans stops services that the current Compose files no longer
# define (e.g. the single pre-split `worker`).
compose up -d --no-deps --remove-orphans "${app_services[@]}"
for service in "${app_services[@]}" caddy; do
  wait_for_service "$service"
done
verify_bff_ready
echo "Application images rolled back to $image_tag. Database migrations are intentionally not downgraded."
