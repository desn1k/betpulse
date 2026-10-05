#!/usr/bin/env bash
# `docker compose` on the production config, with the tag and image digests of
# the release deployed here (the production compose file requires both).
#
#   scripts/prod-compose.sh ps
#   scripts/prod-compose.sh logs -f api
#
# By default it reads .release/last-successful-image-tag and that release's
# .release/<tag>.digests; IMAGE_TAG and RELEASE_DIGESTS override them.
set -Eeuo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state_dir="$root_dir/.release"

image_tag="${IMAGE_TAG:-}"
if [[ -z "$image_tag" && -f "$state_dir/last-successful-image-tag" ]]; then
  image_tag="$(<"$state_dir/last-successful-image-tag")"
fi
if [[ -z "$image_tag" ]]; then
  echo "No release deployed here yet (.release/last-successful-image-tag); set IMAGE_TAG and RELEASE_DIGESTS." >&2
  exit 1
fi

# shellcheck source=scripts/release-digests.sh
. "$root_dir/scripts/release-digests.sh"
load_release_digests "${RELEASE_DIGESTS:-$state_dir/$image_tag.digests}" "$image_tag" || exit 1

IMAGE_TAG="$image_tag" exec docker compose --env-file "$root_dir/.env" \
  -f "$root_dir/infra/docker-compose.yml" \
  -f "$root_dir/infra/docker-compose.prod.yml" "$@"
