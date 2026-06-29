#!/usr/bin/env bash
# Conductor run script — UI (Next.js) for THIS workspace.
#
# Binds $CONDUCTOR_PORT (so Conductor's Open button / preview_urls land here),
# talks to this workspace's backend on $CONDUCTOR_PORT + 1, and tags the build
# with the workspace identity (NEXT_PUBLIC_WORKSPACE_NAME) so the in-app
# WorkspaceBadge shows which worktree you're looking at. Foreground exec (no &)
# so Conductor can stop it cleanly.
set -euo pipefail
cd "${CONDUCTOR_WORKSPACE_PATH:-$PWD}"

UI_PORT="${CONDUCTOR_PORT:-8000}"
BACKEND_PORT="$((UI_PORT + 1))"
BACKEND="http://localhost:${BACKEND_PORT}"

# Ensure node is on PATH (load nvm + honor .nvmrc if needed).
if ! command -v node >/dev/null 2>&1; then
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  # shellcheck disable=SC1091
  [[ -s "$NVM_DIR/nvm.sh" ]] && . "$NVM_DIR/nvm.sh"
  command -v nvm >/dev/null 2>&1 && nvm use >/dev/null 2>&1 || true
fi

# Shell env overrides .env files in Next.js, so this points the UI at the right
# backend and stamps the workspace name into the client bundle.
export BACKEND_URL="$BACKEND"
export NEXT_PUBLIC_BACKEND_URL="$BACKEND"
export NEXT_PUBLIC_WORKSPACE_NAME="${CONDUCTOR_WORKSPACE_NAME:-local}"

cd ui
echo "[ui] workspace=${NEXT_PUBLIC_WORKSPACE_NAME}  port=${UI_PORT}  backend=${BACKEND}"
exec npm run dev -- --port "$UI_PORT"
