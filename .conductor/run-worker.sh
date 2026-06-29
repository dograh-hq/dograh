#!/usr/bin/env bash
# Conductor run script — Arq background worker.
#
# Run this in ONE workspace only. Every workspace shares the same Redis/Postgres,
# so a single arq worker drains the task queue for all of them — multiple workers
# would just fight over the same jobs. Foreground exec so Conductor stops it cleanly.
set -euo pipefail
cd "${CONDUCTOR_WORKSPACE_PATH:-$PWD}"

if [[ -f api/.env ]]; then set -a; # shellcheck disable=SC1091
  source api/.env; set +a; fi

if [[ ! -d venv ]]; then
  echo "ERROR: venv missing. Re-run workspace setup (.conductor/setup.sh)." >&2
  exit 1
fi
# shellcheck disable=SC1091
source venv/bin/activate

echo "[worker] arq worker on shared Redis — workspace=${CONDUCTOR_WORKSPACE_NAME:-?}"
exec python -m arq api.tasks.arq.WorkerSettings --custom-log-dict api.tasks.arq.LOG_CONFIG
