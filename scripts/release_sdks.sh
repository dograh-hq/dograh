#!/usr/bin/env bash
# Cut a release of both SDKs — dograh-sdk (PyPI) and @dograh/sdk (npm) —
# at the given version. Regenerates typed files from node_specs first so
# a stale SDK can't ship.
#
# Usage:
#   ./scripts/release_sdks.sh 0.1.2
#
# Prerequisites (one-time setup):
#   - `build` + `twine` installed: `pip install --upgrade build twine`
#   - `npm login` completed as a member of the `dograh` npm org. npm
#     publish will prompt interactively for a 2FA OTP — run this script
#     in a terminal where you can type the code.
#
# The script is idempotent up to the upload steps: each publish is gated
# by a y/N prompt, so you can dry-run the build and bail before anything
# hits a registry.

set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "usage: $0 <version>   # e.g. 0.1.2" >&2
    exit 1
fi
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.\-][A-Za-z0-9.]+)?$ ]]; then
    echo "error: '$VERSION' does not look like semver (e.g. 0.1.2 or 0.2.0-rc.1)" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

confirm() {
    local reply
    read -r -p "$1 [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

echo "→ Regenerating typed SDK sources from node_specs..."
./scripts/generate_sdk.sh

if ! git diff --quiet -- sdk/python/src/dograh_sdk/typed sdk/typescript/src/typed; then
    echo
    echo "⚠  node_specs regeneration changed typed files. Review the diff"
    echo "   above and commit before releasing — otherwise the tag will"
    echo "   point at a tree that disagrees with what ships to the registry."
    if ! confirm "Continue anyway?"; then
        exit 1
    fi
fi

echo "→ Bumping versions to $VERSION..."
VERSION="$VERSION" python - <<'PY'
import os
import pathlib
import re

version = os.environ["VERSION"]

py = pathlib.Path("sdk/python/pyproject.toml")
py.write_text(
    re.sub(r'^version = "[^"]+"', f'version = "{version}"', py.read_text(), count=1, flags=re.M)
)

ts = pathlib.Path("sdk/typescript/package.json")
ts.write_text(
    re.sub(r'"version": "[^"]+"', f'"version": "{version}"', ts.read_text(), count=1)
)

print(f"  pyproject.toml → {version}")
print(f"  package.json  → {version}")
PY

echo "→ Building Python wheel + sdist..."
(
    cd sdk/python
    rm -rf dist build
    python -m build >/dev/null
    twine check dist/*
)

echo "→ Building TypeScript + running tests..."
(
    cd sdk/typescript
    rm -rf dist
    npm ci --silent
    npm run build
    npm test
)

echo
echo "============================================================"
echo "  Built dograh-sdk==$VERSION and @dograh/sdk@$VERSION"
echo "  Nothing has been published yet."
echo "============================================================"
echo

if confirm "Upload dograh-sdk==$VERSION to TestPyPI first (recommended)?"; then
    (cd sdk/python && twine upload --repository testpypi dist/*)
    echo "  → https://test.pypi.org/project/dograh-sdk/$VERSION/"
    echo
fi

if confirm "Upload dograh-sdk==$VERSION to PyPI?"; then
    (cd sdk/python && twine upload dist/*)
    echo "  → https://pypi.org/project/dograh-sdk/$VERSION/"
    echo
fi

if confirm "Publish @dograh/sdk@$VERSION to npm? (will prompt for 2FA OTP)"; then
    (cd sdk/typescript && npm publish --access public)
    echo "  → https://www.npmjs.com/package/@dograh/sdk/v/$VERSION"
    echo
fi

if confirm "Create annotated git tag sdks-v$VERSION at HEAD?"; then
    git tag -a "sdks-v$VERSION" -m "dograh-sdk + @dograh/sdk $VERSION"
    echo "  → created tag (not pushed). Push with:"
    echo "     git push origin sdks-v$VERSION"
fi

echo "✓ Done."
