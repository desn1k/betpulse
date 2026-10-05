# shellcheck shell=bash
# Sourced by deploy.sh and rollback.sh: read and check a release digests file.
#
# release.yml attaches it to the GitHub Release as release-<version>.digests:
#
#   RELEASE_VERSION=v1.2.3
#   API_IMAGE_DIGEST=sha256:<64 hex>
#   WEB_IMAGE_DIGEST=sha256:<64 hex>
#   MLFLOW_IMAGE_DIGEST=sha256:<64 hex>
#
# The file is parsed, never sourced: only these four keys are accepted.

# load_release_digests FILE VERSION: exports API/WEB/MLFLOW_IMAGE_DIGEST, or
# prints why the file is unusable and returns 1.
load_release_digests() {
  local file="$1" expected="$2"
  local line key value version="" api="" web="" mlflow=""
  if [[ ! -f "$file" ]]; then
    echo "Release digests file not found: $file" >&2
    return 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      RELEASE_VERSION) version="$value" ;;
      API_IMAGE_DIGEST) api="$value" ;;
      WEB_IMAGE_DIGEST) web="$value" ;;
      MLFLOW_IMAGE_DIGEST) mlflow="$value" ;;
      *)
        echo "$file: unknown line '$key'." >&2
        return 1
        ;;
    esac
  done <"$file"
  if [[ "$version" != "$expected" ]]; then
    echo "$file is for release '$version', not '$expected'." >&2
    return 1
  fi
  for value in "$api" "$web" "$mlflow"; do
    if [[ ! "$value" =~ ^sha256:[0-9a-f]{64}$ ]]; then
      echo "$file: every image digest must be sha256:<64 hex>." >&2
      return 1
    fi
  done
  export API_IMAGE_DIGEST="$api" WEB_IMAGE_DIGEST="$web" MLFLOW_IMAGE_DIGEST="$mlflow"
}
