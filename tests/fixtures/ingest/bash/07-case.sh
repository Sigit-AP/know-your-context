#!/bin/bash
# case statements and select menus.
classify() {
  case "$1" in
    0) echo "zero" ;;
    1|2) echo "small" ;;
    [3-9]) echo "single" ;;
    *) echo "big" ;;
  esac
}

menu() {
  select opt in start stop quit; do
    case "${opt}" in
      quit) break ;;
      *) echo "chose ${opt}" ;;
    esac
  done
}
