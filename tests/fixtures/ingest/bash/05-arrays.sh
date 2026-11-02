#!/bin/bash
# Indexed and associative arrays.
NAMES=(alpha beta gamma)
declare -A AGES

ages_load() {
  AGES[alpha]=1
  AGES[beta]=2
}

first_name() {
  echo "${NAMES[0]}"
}

name_count() {
  echo "${#NAMES[@]}"
}
