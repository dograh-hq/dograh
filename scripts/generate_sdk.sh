#!/usr/bin/env bash
# Regenerate typed SDK sources (Python + TypeScript) from the
# authoritative node_specs registry.
#
# Run from anywhere — the script resolves the repo root relative to
# itself. Requires:
#   - `python` with the `api` package importable (conda env `dograh`
#     with `api/.env` sourced, matching the rest of the repo)
#   - `node` (>= 22.6 for native .mts support)
#
# Invoked manually after editing any spec in
# `api/services/workflow/node_specs/`, and by CI (which asserts the
# resulting git diff is empty).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SPECS_JSON="$(mktemp -t dograh-specs-XXXXXX.json)"
trap 'rm -f "$SPECS_JSON"' EXIT

echo "→ Dumping node specs from in-process registry..."
python -m api.services.workflow.node_specs > "$SPECS_JSON"

echo "→ Generating Python typed dataclasses..."
PYTHONPATH="$REPO_ROOT/sdk/python/src" python -m dograh_sdk.codegen \
    --input "$SPECS_JSON" \
    --out "sdk/python/src/dograh_sdk/typed"

echo "→ Generating TypeScript typed interfaces..."
node "sdk/typescript/scripts/codegen.mts" \
    --input "$SPECS_JSON" \
    --out "sdk/typescript/src/typed"

echo "✓ SDK types regenerated from node_specs."
