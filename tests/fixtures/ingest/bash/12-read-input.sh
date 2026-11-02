#!/bin/bash
# Reading input and here-strings.
prompt_name() {
  local name
  read -r -p "name: " name
  echo "${name}"
}

each_line() {
  local line
  while IFS= read -r line; do
    echo "line: ${line}"
  done <<< "$1"
}
