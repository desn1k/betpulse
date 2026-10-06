# shellcheck shell=bash
# Sourced by deploy.sh and rollback.sh: read and check a release digests file,
# record the release running here, and the lock both hold.
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

# record_deployed_release STATE_DIR TAG DIGESTS_FILE: record TAG as the release
# running here (after a successful deploy or rollback). The digests are stored
# first and the tag last, each through a temporary file renamed into place, so
# an interrupted run never leaves a tag without its digests or a torn file.
record_deployed_release() {
  local state_dir="$1" tag="$2" digests="$3" old_umask tmp
  old_umask="$(umask)"
  umask 077
  mkdir -p "$state_dir"
  if [[ "$digests" != "$state_dir/$tag.digests" ]]; then
    tmp="$(mktemp "$state_dir/.$tag.digests.XXXXXX")"
    cp "$digests" "$tmp"
    mv -f "$tmp" "$state_dir/$tag.digests"
  fi
  tmp="$(mktemp "$state_dir/.last-successful-image-tag.XXXXXX")"
  printf '%s\n' "$tag" >"$tmp"
  mv -f "$tmp" "$state_dir/last-successful-image-tag"
  umask "$old_umask"
}

# --- deploy/rollback lock ----------------------------------------------------
# deploy.sh and rollback.sh hold one lock, STATE_DIR/.deploy.lock, from before
# the first docker call until they exit. It is a directory (mkdir is atomic;
# flock is not in Git Bash) holding the owner's PID. Only the process that
# created it removes it, on any exit (success, failure, Ctrl+C, SIGTERM); a
# stale one (owner gone after kill -9 or a crash) is reported, never removed
# automatically. The automatic rollback in deploy.sh runs rollback.sh under its
# parent's lock: BETPULSE_RELEASE_LOCK_PID, accepted only when the lock is held
# by that PID and that PID is the caller's parent. scripts/prod-compose.sh is
# not covered.
release_lock_dir=""
release_lock_owned=false

# _chain_trap COMMAND SIGNAL: run COMMAND after the trap already set (if any).
_chain_trap() {
  local cmd="$1" sig="$2" current
  current="$(trap -p "$sig")"
  if [[ -n "$current" ]]; then
    # trap -p prints `trap -- '<command>' SIG`; re-parse it to get <command>.
    current="$(eval "set -- ${current#trap -- }" && printf '%s' "$1")"
    # shellcheck disable=SC2064  # expand now: the chained command is fixed
    trap -- "$current; $cmd" "$sig"
  else
    # shellcheck disable=SC2064
    trap -- "$cmd" "$sig"
  fi
}

release_release_lock() {
  if [[ "$release_lock_owned" == true && "$(cat "$release_lock_dir/pid" 2>/dev/null)" == "$$" ]]; then
    rm -rf "$release_lock_dir"
  fi
  release_lock_owned=false
}

# acquire_release_lock STATE_DIR: take the lock, or print why not and return 1.
acquire_release_lock() {
  local state_dir="$1" pid
  release_lock_dir="$state_dir/.deploy.lock"
  if [[ -n "${BETPULSE_RELEASE_LOCK_PID:-}" ]]; then
    pid="$(cat "$release_lock_dir/pid" 2>/dev/null || true)"
    if [[ -d "$release_lock_dir" && "$pid" == "$BETPULSE_RELEASE_LOCK_PID" && "$pid" == "$PPID" ]]; then
      return 0 # the automatic rollback, under deploy.sh's lock (never released here)
    fi
    echo "BETPULSE_RELEASE_LOCK_PID=$BETPULSE_RELEASE_LOCK_PID is set, but $release_lock_dir is not held by the deploy.sh that started this run. The variable is only for deploy.sh's automatic rollback: unset it and run again." >&2
    return 1
  fi
  mkdir -p "$state_dir"
  if ! mkdir "$release_lock_dir" 2>/dev/null; then
    pid="$(cat "$release_lock_dir/pid" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Refused: another deploy or rollback is running (pid $pid). Wait for it to finish, then run again." >&2
    else
      echo "Refused: the deploy/rollback lock is stale: its process (pid ${pid:-unknown}) is not running. If no deploy or rollback is running, remove it and run again: rm -rf '$release_lock_dir'" >&2
    fi
    return 1
  fi
  printf '%s\n' "$$" >"$release_lock_dir/pid"
  release_lock_owned=true
  export BETPULSE_RELEASE_LOCK_PID="$$"
  _chain_trap release_release_lock EXIT
  _chain_trap 'exit 130' INT
  _chain_trap 'exit 143' TERM
}
