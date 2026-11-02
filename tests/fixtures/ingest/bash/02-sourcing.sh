#!/bin/bash
# source and dot-command imports.
source ./lib.sh
source "${SCRIPT_DIR}/helpers.sh"
. ./other.sh
. "${HOME}/.env.sh"

boot() {
  lib_init
  other_init
}
