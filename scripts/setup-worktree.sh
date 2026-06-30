#!/usr/bin/env bash
# One-time environment setup for a fresh git worktree.
#
# A new worktree is just a source checkout — it has no venv, the pipecat
# submodule isn't checked out, and ui/node_modules may be missing. This wires
# up an ISOLATED environment so the worktree can run independently (its own
# editable pipecat install points at THIS worktree's pipecat, so pipecat edits
# here take effect).
#
# Heavy (minutes) — deliberately NOT a folderOpen task. Run it once per worktree:
#   ./scripts/setup-worktree.sh        (or VS Code: Run Task -> "Setup worktree environment")
#
# Fast on repeat: uv hardlinks wheels from its global cache, npm uses its cache.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
PYVER="${PYVER:-3.13}"

# Mirror all output to a gitignored, worktree-local log so you can follow
# progress any time this runs (manual, VS Code task, or background):
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

echo "✅ Worktree env ready: $(basename "$ROOT")  ($(python -V 2>&1))"
