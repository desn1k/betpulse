#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$root_dir/.env"
state_dir="$root_dir/.release"
last_successful_tag_file="$state_dir/last-successful-image-tag"
health_attempts="${DEPLOY_HEALTHCHECK_ATTEMPTS:-30}"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Copy .env.example and configure production secrets first." >&2
  exit 1
fi

image_tag="${IMAGE_TAG:-}"
if [[ -z "$image_tag" || "$image_tag" == "latest" || ! "$image_tag" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]]; then
  echo "Set IMAGE_TAG to an immutable published release tag (for example v1.2.3)." >&2
  exit 1
fi

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

previous_tag=""
if [[ -f "$last_successful_tag_file" ]]; then
  previous_tag="$(<"$last_successful_tag_file")"
fi

rollback_on_failure() {
  local exit_code="$?"
  if [[ -n "$previous_tag" && "$previous_tag" != "$image_tag" ]]; then
    echo "Deployment failed; restoring application images tagged $previous_tag." >&2
    image_tag="$previous_tag"
    compose pull api worker web || true
    compose up -d --no-deps api worker web || true
  fi
  exit "$exit_code"
}
trap rollback_on_failure ERR

compose pull api worker web
compose up -d postgres redis minio
wait_for_service postgres
wait_for_service redis

compose run --rm api alembic upgrade head
compose up -d --remove-orphans
wait_for_service api
wait_for_service web
wait_for_service caddy

mkdir -p "$state_dir"
umask 077
printf '%s\n' "$image_tag" > "$last_successful_tag_file"
echo "Deployment of $image_tag completed successfully."
