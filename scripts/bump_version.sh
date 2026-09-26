#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$ROOT_DIR/VERSION"

usage() {
  echo "Usage: $0 X.Y.Z[.N]" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
new_version="$1"
[[ "$new_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || usage

current_version="$(tr -d '[:space:]' < "$VERSION_FILE")"
[[ "$current_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || {
  echo "Current VERSION is invalid: $current_version" >&2
  exit 1
}

if [[ "$new_version" == "$current_version" ]]; then
  echo "Version is unchanged: $current_version" >&2
  exit 1
fi

if [[ "$(printf '%s\n' "$current_version" "$new_version" | sort -V | tail -n1)" != "$new_version" ]]; then
  echo "New version must be greater than $current_version" >&2
  exit 1
fi

VERSION_VALUE="$new_version" ROOT_DIR="$ROOT_DIR" python3 - <<'PY'
from pathlib import Path
import os
import re

root = Path(os.environ["ROOT_DIR"])
version = os.environ["VERSION_VALUE"]

(root / "VERSION").write_text(version + "\n")

pyproject = root / "api/pyproject.toml"
pyproject_text = pyproject.read_text()
pyproject_text, count = re.subn(
    r'(?m)^version = "[^"]+"$',
    f'version = "{version}"',
    pyproject_text,
    count=1,
)
if count != 1:
    raise SystemExit("Could not update api/pyproject.toml version")
pyproject.write_text(pyproject_text)

for filename in ("ui/package.json", "ui/package-lock.json"):
    path = root / filename
    text = path.read_text()
    text, count = re.subn(
        r'("version": ")[^"]+("(?:,|\n))',
        rf'\g<1>{version}\g<2>',
        text,
        count=2 if filename.endswith("package-lock.json") else 1,
    )
    if count != (2 if filename.endswith("package-lock.json") else 1):
        raise SystemExit(f"Could not update {filename} version")
    path.write_text(text)
PY

"$ROOT_DIR/scripts/check_version_sync.sh"
echo "Version bumped to $new_version"
