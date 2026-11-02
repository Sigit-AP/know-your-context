#!/bin/bash
# Basic function definitions (both forms).
add() {
  echo $((${1:-0} + ${2:-0}))
}

greet() {
  local name="${1:-world}"
  echo "hello, ${name}"
}

function die {
  echo "error: $*" >&2
  exit 1
}
