#!/bin/bash
# Function calls parking unresolved command refs.
helper() {
  echo $(( $1 * 2 ))
}

run() {
  local total
  total=$(helper "$1")
  helper "${total}"
}
