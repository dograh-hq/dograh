#!/usr/bin/env bash
#
# entrypoint-wrapper.sh — wrapper that sources /docker-entrypoint.d/ scripts
# before delegating to the andrius/asterisk:22 base image entrypoint.
#
# The base image's /usr/local/bin/entrypoint.sh does NOT source
# /docker-entrypoint.d/*.sh (unlike the standard docker-entrypoint.sh).
# This wrapper fills that gap so our config-rendering script runs first.
#
# We match any executable file in /docker-entrypoint.d/ (the convention
# in entrypoint.sh is *.sh, but we also accept extensionless scripts
# like our 00-render-asterisk that the Dockerfile installs).
#
set -euo pipefail

# Source all docker-entrypoint.d scripts (standard ordering)
shopt -s nullglob
for f in /docker-entrypoint.d/*; do
  if [ -f "$f" ] && [ -x "$f" ]; then
    echo "[docker-entrypoint] executing $f"
    "$f"
  fi
done
shopt -u nullglob

# Then exec the base image's entrypoint with all received arguments
# (the base entrypoint does uid/chown setup then execs asterisk)
exec /usr/local/bin/entrypoint.sh "$@"
