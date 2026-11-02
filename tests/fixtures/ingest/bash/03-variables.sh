#!/bin/bash
# Constants (uppercase), locals and exported variables.
MAX_RETRIES=3
DEFAULT_TIMEOUT=30
export PATH_PREFIX="/opt/bin"

counter=0

bump() {
  local amount="${1:-1}"
  counter=$((counter + amount))
  echo "${counter}"
}
