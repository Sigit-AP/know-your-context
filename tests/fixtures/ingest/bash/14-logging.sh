#!/bin/bash
# Logging helpers with levels.
LOG_LEVEL="${LOG_LEVEL:-info}"

_log() {
  local level="$1"
  shift
  printf '%s [%s] %s
' "$(date +%H:%M:%S)" "${level}" "$*" >&2
}

log_info() {
  _log INFO "$@"
}

log_error() {
  _log ERROR "$@"
}
