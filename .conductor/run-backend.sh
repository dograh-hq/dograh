#!/usr/bin/env bash
# Conductor run script — Backend (FastAPI/uvicorn) for THIS workspace.
#
# Binds $CONDUCTOR_PORT + 1 and points CORS / UI_APP_URL at this workspace's UI
# (which runs on $CONDUCTOR_PORT). Runs in the FOREGROUND with exec (no &) so
# Conductor's SIGHUP cleanly tears it down.
set -euo pipefail
cd "${CONDUCTOR_WORKSPACE_PATH:-$PWD}"

UI_PORT="${CONDUCTOR_PORT:-8000}"
BACKEND_PORT="$((UI_PORT + 1))"

# Load the workspace's api/.env, then override the port-specific bits. The
# backend reads config via os.getenv, and these exports happen after the source,
# so they win over the values copied from the main checkout (which assume 8000/3000).
if [[ -f api/.env ]]; then set -a; # shellcheck disable=SC1091
  source api/.env; set +a; fi
export FASTAPI_PORT="$BACKEND_PORT"
export UI_APP_URL="http://localhost:${UI_PORT}"
export CORS_ALLOWED_ORIGINS="http://localhost:${UI_PORT},http://127.0.0.1:${UI_PORT}"

if [[ ! -d venv ]]; then
  echo "ERROR: venv missing. Re-run workspace setup (.conductor/setup.sh)." >&2
  exit 1
fi
# shellcheck disable=SC1091
source venv/bin/activate

echo "[backend] workspace=${CONDUCTOR_WORKSPACE_NAME:-?}  port=${BACKEND_PORT}  ui=${UI_PORT}"
exec uvicorn api.app:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload --reload-dir api
