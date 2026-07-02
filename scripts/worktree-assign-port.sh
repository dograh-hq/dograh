#!/usr/bin/env bash
# Assign a unique backend port to this git worktree and rewrite the env files
# that depend on it. Runs automatically as a VS Code "folderOpen" task (see
# .vscode/tasks.json), so it executes once per worktree when you open it.
#
# Scheme:
#   - The MAIN worktree is left untouched (backend stays on uvicorn's default 8000).
#   - Each linked worktree gets the next free backend port: 8001, 8002, ...
#   - api/.env       : UVICORN_PORT          -> the assigned backend port
#   - ui/.env  : BACKEND_URL           -> http://localhost:<port>
#                      NEXT_PUBLIC_BACKEND_URL -> http://localhost:<port>
#
# CORS is intentionally NOT touched: local dev runs DEPLOYMENT_MODE="oss", where
# the API forces allow_origins=["*"] and ignores CORS_ALLOWED_ORIGINS entirely.
#
# Idempotent: re-running keeps an already-assigned, non-colliding port. The UI
# dev server is left alone — `npm run dev` auto-selects a free port (3000, 3001…).
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
MAIN="$(git worktree list --porcelain | sed -n '1s/^worktree //p')"
[ "$ROOT" = "$MAIN" ] && { echo "[worktree] main worktree -> backend 8000 (untouched)"; exit 0; }

AENV="$ROOT/api/.env"
UENV="$ROOT/ui/.env"
[ -f "$AENV" ] || { echo "[worktree] no api/.env yet; skipping"; exit 0; }

# Echo the UVICORN_PORT value from an env file (empty if unset/missing).
port_of() { { grep -E '^[[:space:]]*UVICORN_PORT=' "$1" 2>/dev/null | tail -1 | sed -E 's/^[^=]*=//; s/[[:space:]]//g'; } || true; }

# Ports already in use by OTHER worktrees (main implicitly uses 8000).
used=(8000)
while IFS= read -r line; do
  case "$line" in
    "worktree "*)
      wt="${line#worktree }"
      [ "$wt" = "$ROOT" ] && continue
      p="$(port_of "$wt/api/.env")"
      [ -n "$p" ] && used+=("$p")
      ;;
  esac
done < <(git worktree list --porcelain)

mine="$(port_of "$AENV")"

# Keep my port if it's set and not claimed by another worktree; else take max+1.
reassign=1
if [ -n "$mine" ]; then
  reassign=0
  for u in "${used[@]}"; do [ "$u" = "$mine" ] && reassign=1; done
fi
if [ "$reassign" -eq 1 ]; then
  max=0
  for u in "${used[@]}"; do [ "$u" -gt "$max" ] && max="$u"; done
  B=$((max + 1))
else
  B="$mine"
fi

# Insert or update KEY=VALUE in an env file, preserving everything else.
upsert() {
  local key="$1" val="$2" file="$3"
  if grep -qE "^[[:space:]]*${key}=" "$file"; then
    sed -i.bak -E "s|^[[:space:]]*${key}=.*|${key}=${val}|" "$file" && rm -f "$file.bak"
  else
    printf '\n%s=%s\n' "$key" "$val" >> "$file"
  fi
}

upsert UVICORN_PORT "$B" "$AENV"
if [ -f "$UENV" ]; then
  upsert BACKEND_URL             "http://localhost:$B" "$UENV"
  upsert NEXT_PUBLIC_BACKEND_URL "http://localhost:$B" "$UENV"
fi

echo "[worktree] $(basename "$ROOT"): backend=$B (UI auto-port via 'npm run dev')"
