#!/usr/bin/env bash
# Conductor run script — full dev stack (UI + backend) for THIS workspace.
#
# A Conductor workspace runs ONE run script at a time (run_mode governs
# concurrency ACROSS workspaces, not multiple run buttons within one). So to get
# the UI AND the backend up together, we launch both with `concurrently`.
#
# Conductor stops a run with SIGHUP (then SIGKILL after 200ms). Neither npx nor
# concurrently reliably tears down their child tree on SIGHUP, so we supervise:
# trap the signal and recursively kill the whole tree ourselves. We do NOT exec,
# so this shell stays alive to handle the trap.
#
# Ports: UI on $CONDUCTOR_PORT, backend on $CONDUCTOR_PORT + 1.
set -uo pipefail
cd "${CONDUCTOR_WORKSPACE_PATH:-$PWD}"

UI_PORT="${CONDUCTOR_PORT:-8000}"
BACKEND_PORT="$((UI_PORT + 1))"

# Ensure node/npx is on PATH (load nvm + honor .nvmrc if needed).
if ! command -v npx >/dev/null 2>&1; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  # shellcheck disable=SC1091
  [[ -s "$NVM_DIR/nvm.sh" ]] && . "$NVM_DIR/nvm.sh"
  command -v nvm >/dev/null 2>&1 && nvm use >/dev/null 2>&1 || true
fi

# Prefer a locally-installed concurrently (fast); fall back to npx, which fetches
# it once then caches — so this also works in workspaces created before it existed.
if [[ -x ui/node_modules/.bin/concurrently ]]; then
  RUNNER=(ui/node_modules/.bin/concurrently)
else
  RUNNER=(npx --yes concurrently)
fi

# Recursively SIGTERM a process and every descendant (children first). npm/npx/
# next don't reliably forward signals, so we signal each PID in the tree directly.
kill_tree() {
  local pid=$1 child
  for child in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$child"; done
  kill -TERM "$pid" 2>/dev/null || true
}

shutdown() {
  trap - HUP INT TERM EXIT
  [[ -n "${CHILD:-}" ]] && kill_tree "$CHILD"
  exit 0
}
trap shutdown HUP INT TERM EXIT

echo "[dev] workspace=${CONDUCTOR_WORKSPACE_NAME:-?}  ui=:${UI_PORT}  backend=:${BACKEND_PORT}"
"${RUNNER[@]}" \
  --names "ui,backend" \
  --prefix-colors "magenta,cyan" \
  --kill-others \
  "bash .conductor/run-ui.sh" \
  "bash .conductor/run-backend.sh" &
CHILD=$!
wait "$CHILD"
