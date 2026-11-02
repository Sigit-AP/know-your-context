#!/bin/bash
# Subshells, command groups and arithmetic.
sum_all() {
  local total=0
  for v in "$@"; do
    ((total += v))
  done
  echo "${total}"
}

in_subshell() {
  (cd /tmp && pwd)
  { echo "a"; echo "b"; } | wc -l
}
