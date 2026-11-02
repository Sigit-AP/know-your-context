#!/bin/bash
# Argument parsing with getopts and shift loops.
parse_args() {
  local verbose=0
  while getopts ":vo:" opt; do
    case "${opt}" in
      v) verbose=1 ;;
      o) OUTPUT_FILE="${OPTARG}" ;;
      *) return 1 ;;
    esac
  done
  shift $((OPTIND - 1))
  echo "${verbose} $*"
}
