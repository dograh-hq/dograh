#!/usr/bin/env bash
# Conductor setup script — runs ONCE when a workspace (git worktree) is created.
#
# A fresh worktree only has git-tracked files, so this recreates the rest: the
# gitignored env files, the pipecat submodule checkout, a Python venv,
# ui/node_modules, the shared local Docker stack, and the DB schema.
#
# Conductor injects: CONDUCTOR_ROOT_PATH (main checkout), CONDUCTOR_WORKSPACE_PATH,
# CONDUCTOR_WORKSPACE_NAME, CONDUCTOR_PORT (first of 10), CONDUCTOR_IS_LOCAL.
set -euo pipefail

ROOT="${CONDUCTOR_ROOT_PATH:-}"
WS="${CONDUCTOR_WORKSPACE_PATH:-$PWD}"
cd "$WS"

log() { printf '\n\033[1;36m[conductor-setup]\033[0m %s\n' "$*"; }

# 1) Copy the gitignored env files from the main checkout. These hold real
# secrets, so they're never committed — a fresh worktree won't have them. We copy
# only what's missing (idempotent), so re-running setup won't clobber local edits.
# (See README "Environment files" for the canonical list.)
if [[ -n "$ROOT" && "$ROOT" != "$WS" ]]; then
  log "Copying gitignored env files from $ROOT"
  for f in api/.env api/.env.test ui/.env ui/.env.local ui/.env.sentry-build-plugin; do
    if [[ ! -f "$f" && -f "$ROOT/$f" ]]; then
      mkdir -p "$(dirname "$f")"
      cp "$ROOT/$f" "$f"
      echo "  copied $f"
    fi
  done
fi

# 2) pipecat submodule — REQUIRED here, NOT redundant with step 3.
# A fresh git worktree has an empty pipecat/. setup_requirements.sh --dev
# (step 3) deliberately SKIPS `git submodule update` (it assumes CI already
# checked out submodules) but still runs `uv pip install -e ./pipecat`, which
# fails unless the checkout is already on disk. So we populate it first.
log "Initializing git submodules (pipecat)"
git submodule update --init --recursive

# 3) Python venv with 3.13 + backend/pipecat deps ----------------------------
# Bare `python3` may be 3.14 here; setup_requirements.sh requires 3.12/3.13.
PY313="$(command -v python3.13 || true)"
if [[ -z "$PY313" ]]; then
  echo "ERROR: python3.13 not found. Install it (e.g. brew install python@3.13)." >&2
  exit 1
fi
if [[ ! -d venv ]]; then
  log "Creating venv with $PY313 ($("$PY313" --version))"
  "$PY313" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
log "Installing backend + pipecat deps (uv) — this is the slow step"
bash scripts/setup_requirements.sh --dev

# 4) UI deps -----------------------------------------------------------------
ensure_node() {
  if ! command -v node >/dev/null 2>&1; then
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    # shellcheck disable=SC1091
    [[ -s "$NVM_DIR/nvm.sh" ]] && . "$NVM_DIR/nvm.sh"
    command -v nvm >/dev/null 2>&1 && nvm use >/dev/null 2>&1 || true
  fi
}
ensure_node
log "Installing UI deps (npm install)"
( cd ui && npm install )

# 5) Shared local Docker stack (idempotent, pinned project name) -------------
# All workspaces share ONE stack named "dograh" so they never collide on the
# fixed Postgres/Redis/MinIO ports. If it's already up this is a no-op.
if command -v docker >/dev/null 2>&1; then
  log "Ensuring shared Docker stack (COMPOSE_PROJECT_NAME=dograh)"
  COMPOSE_PROJECT_NAME=dograh docker compose -f docker-compose-local.yaml up -d \
    || echo "  (warning: could not start docker stack; start it manually)"
else
  echo "  (docker not found; start Postgres/Redis/MinIO yourself)"
fi

# 6) DB migrations (best-effort; shared DB, alembic is idempotent) -----------
if [[ -f api/.env ]]; then
  log "Running DB migrations (alembic upgrade head)"
  set -a; # shellcheck disable=SC1091
  source api/.env; set +a
  alembic -c api/alembic.ini upgrade head || echo "  (warning: migrations skipped/failed — is the DB up?)"
fi

PORT="${CONDUCTOR_PORT:-8000}"
log "Setup complete for '${CONDUCTOR_WORKSPACE_NAME:-?}'."
echo "  Run menu: backend -> :${PORT}   ui -> :$((PORT + 1))   worker -> shared arq"
