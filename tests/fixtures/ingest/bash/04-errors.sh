#!/bin/bash
# set -e style guards, trap handlers and status checks.
set -euo pipefail

cleanup() {
  rm -f "${TMPFILE:-/tmp/x}"
}

trap cleanup EXIT

checked_run() {
  local cmd="$1"
  if ! "${cmd}"; then
    echo "failed: ${cmd}" >&2
    return 1
  fi
}

guarded() {
  checked_run true || exit 2
}
