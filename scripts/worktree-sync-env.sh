#!/usr/bin/env bash
# Localize this git worktree's env files. Runs automatically as a VS Code
# "folderOpen" task (see .vscode/tasks.json), so it executes every time you open
# the worktree. Three jobs, all idempotent:
#
#   1. Repoint absolute paths that still point at the main checkout.
#   2. Assign a unique backend port (UVICORN_PORT).
#   3. Assign a unique UI port (UI_PORT), and derive the backend's CORS
#      allowlist + UI_APP_URL from it.
#
# Why (1) is needed: VS Code's git.worktreeIncludeFiles (.vscode/settings.json)
# copies api/.env, api/.env.test and ui/.env into a new worktree BYTE FOR BYTE.
# Any value holding an absolute path -- PYTHONPATH, LOG_FILE_PATH, ... -- still
# points at the main checkout, so console entry points (arq, alembic, the
# `pytest` binary; anything whose sys.path[0] is venv/bin rather than the cwd)
# would import the MAIN worktree's api/ while you edit this one.
#
# Why (3) is needed: with DEPLOYMENT_MODE="saas" (the usual local setting for
# this repo's maintainers) api/app.py enforces CORS_ALLOWED_ORIGINS with
# credentials, and UI_APP_URL backs embed URLs + billing return URLs. Both
# hard-code a UI origin, so they have to follow this worktree's UI port. Under
# DEPLOYMENT_MODE="oss" the API forces allow_origins=["*"] and ignores
# CORS_ALLOWED_ORIGINS entirely, so writing it is harmless there.
#
# Port scheme (same algorithm for both ports):
#   - The MAIN worktree is left untouched: backend 8000, UI 3000.
#   - Each linked worktree takes the LOWEST port above those that is neither
#     claimed by another worktree nor held by a foreign listener: 8001/3001,
#     8002/3002, ...
#   - api/.env : UVICORN_PORT, CORS_ALLOWED_ORIGINS, UI_APP_URL
#   - ui/.env  : UI_PORT, BACKEND_URL, NEXT_PUBLIC_BACKEND_URL
#
# There is deliberately no central registry: the env files ARE the claims, and
# sibling worktrees are scanned for theirs. Worktrees here are disposable, so
# removing one has to free its ports with no bookkeeping -- a registry under
# ~/.local/state would instead accumulate an entry per removed worktree and need
# a GC pass to stay correct. Its one advantage (a branch keeping its port across
# delete-and-recreate) is worthless when worktrees are never recreated.
#
# UI_PORT is consumed by ui/scripts/dev-server.mjs (behind `npm run dev`). Note
# that Next still auto-increments off a busy port even when given an explicit
# -p, so if something else is squatting on the assigned port the UI moves and
# the derived CORS/UI_APP_URL entries go stale -- restart it on a free port.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
MAIN="$(git worktree list --porcelain | sed -n '1s/^worktree //p')"
[ "$ROOT" = "$MAIN" ] && { echo "[worktree] main worktree -> backend 8000, ui 3000 (untouched)"; exit 0; }

AENV="$ROOT/api/.env"
UENV="$ROOT/ui/.env"

# --- 1. absolute paths -------------------------------------------------------

# Rewrite every "$MAIN/..." path in an env file to the same path under "$ROOT".
# $ROOT can itself contain $MAIN as a string prefix (.../dograh versus
# .../dograh.worktrees/x), so already-correct occurrences are parked on a
# sentinel character first -- otherwise a second run would rewrite the paths the
# first run just produced.
SENTINEL="$(printf '\001')"
repoint_paths() {
  local file="$1" tmp
  [ -f "$file" ] || return 0
  tmp="$file.worktree.tmp"
  sed "s%${ROOT}%${SENTINEL}%g; s%${MAIN}%${ROOT}%g; s%${SENTINEL}%${ROOT}%g" "$file" > "$tmp"
  if cmp -s "$file" "$tmp"; then
    rm -f "$tmp"
  else
    mv "$tmp" "$file"
    echo "[worktree] repointed paths in ${file#"$ROOT"/}"
  fi
}

repoint_paths "$AENV"
repoint_paths "$ROOT/api/.env.test"
repoint_paths "$UENV"

# --- 2/3. ports --------------------------------------------------------------

# Echo the value of KEY in an env file (empty if unset/missing), quotes stripped.
value_of() {
  { grep -E "^[[:space:]]*$2=" "$1" 2>/dev/null | tail -1 \
      | sed -E 's/^[^=]*=//; s/[[:space:]]//g; s/^"//; s/"$//'; } || true
}

# True when port $1 has a listener that does NOT belong to this worktree.
#
# Listeners rooted in this worktree are deliberately treated as "not taken":
# this task re-runs on every folder open, and a running uvicorn/next would
# otherwise look like an occupied port and get reassigned out from under itself
# -- leaving the derived CORS/BACKEND_URL entries pointing somewhere the server
# never moved to. Anything else (another project, Docker, MinIO) does count.
#
# Without lsof no listener is visible, so every port reads as free and
# allocation degrades to claim-scanning alone.
foreign_listener() {
  local pid cwd
  for pid in $(lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true); do
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    case "$cwd" in
      "$ROOT" | "$ROOT"/*) ;;
      *) return 0 ;;
    esac
  done
  return 1
}

# Echo this worktree's port for KEY, read from the env file at REL in each
# worktree. Keeps the current value while it is still available, otherwise takes
# the LOWEST available port above BASE -- lowest-first refills the gaps left by
# removed worktrees instead of drifting upward forever. Available means claimed
# by no other worktree and free of foreign listeners. BASE is the main
# worktree's port; main is never rewritten, so it holds no value to scan and is
# reserved unconditionally.
assign_port() {
  local key="$1" rel="$2" base="$3"
  local claimed=" $base " wt p mine line

  while IFS= read -r line; do
    case "$line" in
      "worktree "*)
        wt="${line#worktree }"
        [ "$wt" = "$ROOT" ] && continue
        p="$(value_of "$wt/$rel" "$key")"
        case "$p" in ''|*[!0-9]*) ;; *) claimed="$claimed$p " ;; esac
        ;;
    esac
  done < <(git worktree list --porcelain)

  mine="$(value_of "$ROOT/$rel" "$key")"
  case "$mine" in ''|*[!0-9]*) mine="" ;; esac
  if [ -n "$mine" ]; then
    case "$claimed" in
      *" $mine "*) ;;
      *) if ! foreign_listener "$mine"; then echo "$mine"; return 0; fi ;;
    esac
  fi

  for ((p = base + 1; p <= base + 99; p++)); do
    case "$claimed" in *" $p "*) continue ;; esac
    if foreign_listener "$p"; then continue; fi
    echo "$p"
    return 0
  done

  echo "[worktree] no free $key between $((base + 1)) and $((base + 99))" >&2
  return 1
}

# Insert or update KEY=VALUE in an env file, preserving everything else.
upsert() {
  local key="$1" val="$2" file="$3"
  if grep -qE "^[[:space:]]*${key}=" "$file"; then
    sed -i.bak -E "s|^[[:space:]]*${key}=.*|${key}=${val}|" "$file" && rm -f "$file.bak"
  else
    printf '\n%s=%s\n' "$key" "$val" >> "$file"
  fi
}

[ -f "$AENV" ] || { echo "[worktree] no api/.env yet; skipping port assignment"; exit 0; }

B="$(assign_port UVICORN_PORT api/.env 8000)"
upsert UVICORN_PORT "$B" "$AENV"

if [ -f "$UENV" ]; then
  U="$(assign_port UI_PORT ui/.env 3000)"
  upsert UI_PORT "$U" "$UENV"
  upsert BACKEND_URL             "http://localhost:$B" "$UENV"
  upsert NEXT_PUBLIC_BACKEND_URL "http://localhost:$B" "$UENV"
  upsert CORS_ALLOWED_ORIGINS "\"http://localhost:$U\"" "$AENV"
  upsert UI_APP_URL           "\"http://localhost:$U\"" "$AENV"
  echo "[worktree] $(basename "$ROOT"): backend=$B ui=$U (CORS + UI_APP_URL follow ui)"
else
  echo "[worktree] $(basename "$ROOT"): backend=$B (no ui/.env; UI port unassigned)"
fi
