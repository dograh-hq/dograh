#!/usr/bin/env bash
# Environment setup for a git worktree: pipecat submodule, isolated venv,
# Python --dev deps, and ui/node_modules. A fresh worktree is just a source
# checkout, so it has none of these; this provisions an ISOLATED environment
# (its own editable pipecat install points at THIS worktree's pipecat, so
# pipecat edits here take effect).
#
# Runs automatically once per worktree via the "folderOpen" task in
# .vscode/tasks.json. A success sentinel (venv/.worktree-setup-complete) makes
# it run-once:
#   --if-needed : exit immediately if already provisioned (used by folderOpen)
#   (no flag)   : always run / re-provision (the manual "force" task)
#
# Heavy (minutes) the first time; instant skip afterwards. uv hardlinks wheels
# from its global cache and npm uses its cache, so even a forced re-run is fast.
set -euo pipefail

IF_NEEDED=0
for arg in "$@"; do
  case "$arg" in
    --if-needed) IF_NEEDED=1 ;;
    *) echo "Unknown argument: $arg" >&2; echo "Usage: $0 [--if-needed]" >&2; exit 1 ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
PYVER="${PYVER:-3.13}"
SENTINEL="$ROOT/venv/.worktree-setup-complete"

# Run-once guard: skip instantly when already provisioned. Checked BEFORE the log
# is (re)written so a skip never clobbers the previous run's log. The sentinel
# lives inside venv/, so deleting venv/ (or the worktree) forces a redo; an
# interrupted run never writes it, so the next open self-heals.
if [ "$IF_NEEDED" -eq 1 ] && [ -f "$SENTINEL" ]; then
  echo "[setup-worktree] already provisioned ($SENTINEL) — skipping."
  exit 0
fi

# Mirror all output to a gitignored, worktree-local log so you can follow
# progress any time this runs (folderOpen task, manual, or background):
#   tail -f logs/setup-worktree.log
# (/logs/ is already in .gitignore, and each worktree has its own logs/.)
LOG="$ROOT/logs/setup-worktree.log"
mkdir -p "$ROOT/logs"
exec > >(tee "$LOG") 2>&1
echo "=== setup-worktree $(date '+%Y-%m-%d %H:%M:%S')  [$(basename "$ROOT")] ==="

echo "==> [1/4] pipecat submodule (init/update for this worktree)..."
git submodule update --init --recursive

echo "==> [2/4] isolated venv (python $PYVER)..."
if [ -x venv/bin/python ]; then
  echo "    venv already exists — reusing."
else
  uv venv venv --python "$PYVER"
fi
# Activate so setup_requirements.sh / uv install into THIS worktree's venv.
set +u  # activate scripts can reference unset vars
# shellcheck disable=SC1091
source venv/bin/activate
set -u

echo "==> [3/4] Python deps (--dev; submodule already inited)..."
./scripts/setup_requirements.sh --dev

echo "==> [4/4] UI node_modules..."
( cd ui && npm install )

# Mark success LAST, so an interrupted run re-provisions on the next open.
touch "$SENTINEL"
echo "✅ Worktree env ready: $(basename "$ROOT")  ($(python -V 2>&1))"
