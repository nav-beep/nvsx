#!/usr/bin/env bash
# Screens for recording. Enter between each. No commentary.
set -u
cd "$(dirname "$0")"

YELLOW=$'\e[1;33m'
RESET=$'\e[0m'

next() {
  echo ""
  echo -n "  ${YELLOW}Enter →${RESET} "
  read -r
  clear
}

clear
less -R examples/legacy-xid79-runbook.md
next

./nvsx list
next

./nvsx show gpu-off-bus-recover
next

./nvsx doctor
next

cat runbooks/hooks/gpu-off-bus-recover/on-remediate.sh
next

./nvsx selftest gpu-off-bus-recover

exec "${SHELL:-/bin/bash}"
