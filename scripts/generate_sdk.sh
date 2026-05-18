#!/usr/bin/env bash
# Regenerate every file the SDKs derive from authoritative backend state:
#
#   1. Typed node dataclasses / TS interfaces (from node_specs registry)
#   2. Filtered OpenAPI spec (routes tagged via @sdk_expose)
#   3. Pydantic request/response models + TS interfaces (datamodel-codegen
#      / openapi-typescript)
#   4. Client method mixins (_generated_client.py / _generated_client.ts)
#   5. Full OpenAPI spec for the Mintlify docs site
#      (docs/api-reference/openapi.json)
#
# Run from anywhere — the script resolves the repo root relative to itself.
# Requires:
#   - `python` in the `dograh` conda env, `api/.env` sourced; the `api`
#     package must be importable. `datamodel-code-generator` installed
#     (`pip install datamodel-code-generator`).
#   - `node` (>= 22.6 for native .mts support) and npm. openapi-typescript
#     is a devDependency of sdk/typescript; `npm install` in that dir is
#     done for you if node_modules is missing.
#
# Invoked manually after editing any NodeSpec or after adding/removing an
# `@sdk_expose` decorator. CI runs this and asserts the git diff is empty.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -f "$REPO_ROOT/api/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/api/.env"
    set +a
fi

SPECS_JSON="$(mktemp -t dograh-specs-XXXXXX.json)"
OPENAPI_JSON="$(mktemp -t dograh-openapi-XXXXXX.json)"
trap 'rm -f "$SPECS_JSON" "$OPENAPI_JSON"' EXIT

# ── 1. Node-spec typed dataclasses ────────────────────────────────────

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

# ── 2. SDK-scoped OpenAPI spec ────────────────────────────────────────

echo "→ Dumping filtered OpenAPI (sdk_expose routes only)..."
python - <<PY
import json
from loguru import logger
logger.remove()
from fastapi.openapi.utils import get_openapi
from api.app import app

sdk_routes = [
    r for r in app.routes
    if getattr(r, "openapi_extra", None)
    and "x-sdk-method" in (r.openapi_extra or {})
]
spec = get_openapi(title=app.title, version=app.version, routes=sdk_routes)
with open("$OPENAPI_JSON", "w") as f:
    json.dump(spec, f)
print(f"  → {len(sdk_routes)} operations, "
      f"{len(spec.get('components', {}).get('schemas', {}))} schemas reachable")
PY

# ── 3. Request/response models (off-the-shelf) ────────────────────────

echo "→ Generating Python Pydantic models (datamodel-codegen)..."
datamodel-codegen \
    --input "$OPENAPI_JSON" \
    --input-file-type openapi \
    --output "sdk/python/src/dograh_sdk/_generated_models.py" \
    --output-model-type pydantic_v2.BaseModel \
    --target-python-version 3.10 \
    --use-schema-description \
    --use-field-description \
    --use-annotated \
    --use-union-operator \
    --field-constraints \
    --wrap-string-literal

echo "→ Generating TypeScript types (openapi-typescript)..."
if [ ! -d "sdk/typescript/node_modules" ]; then
    (cd sdk/typescript && npm install --silent)
fi
(cd sdk/typescript && npx --no-install openapi-typescript \
    "$OPENAPI_JSON" \
    --output "src/_generated_models.ts" \
    --root-types \
    --root-types-no-schema-prefix)

# ── 4. Client method mixins ──────────────────────────────────────────

echo "→ Emitting client method mixins..."
python -m sdk.codegen.client_codegen \
    --input "$OPENAPI_JSON" \
    --py-out "sdk/python/src/dograh_sdk/_generated_client.py" \
    --ts-out "sdk/typescript/src/_generated_client.ts"

# ── 5. Docs OpenAPI spec ─────────────────────────────────────────────

echo "→ Dumping full OpenAPI spec for docs site..."
python -m scripts.dump_docs_openapi

echo "✓ SDK regenerated."
