#!/usr/bin/env bash
# Validate a release version and say whether it is a stable release.
#
#   scripts/release-version.sh v1.2.3      -> prints "stable=true"
#   scripts/release-version.sh v1.2.3-rc1  -> prints "stable=false"
#   scripts/release-version.sh 1.2         -> exit 1
#
# Only a stable release (vMAJOR.MINOR.PATCH, no suffix) also gets the `latest`
# tag in GHCR; pre-releases (-rc1, -beta.2, ...) never do. release.yml appends
# the output to $GITHUB_OUTPUT.
set -Eeuo pipefail

version="${1:-}"
if [[ ! "$version" =~ ^v[0-9]+(\.[0-9]+){2}([-.][0-9A-Za-z.-]+)?$ ]]; then
  echo "Release version must use vMAJOR.MINOR.PATCH format (got '$version')." >&2
  exit 1
fi
if [[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "stable=true"
else
  echo "stable=false"
fi
