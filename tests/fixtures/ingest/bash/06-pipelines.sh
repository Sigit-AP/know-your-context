#!/bin/bash
# Pipelines and command substitution (calls = commands).
count_lines() {
  wc -l < "$1"
}

total_lines() {
  local total=0
  for f in "$@"; do
    total=$((total + $(count_lines "$f")))
  done
  echo "${total}"
}

list_logs() {
  find . -name "*.log" | sort | head -n 10
}
