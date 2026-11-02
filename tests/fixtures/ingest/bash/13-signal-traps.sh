#!/bin/bash
# Signal traps and ERR trap.
on_err() {
  echo "error at line $1" >&2
}

on_int() {
  echo "interrupted" >&2
  exit 130
}

install_traps() {
  trap 'on_err ${LINENO}' ERR
  trap on_int INT
  trap on_int TERM
}
