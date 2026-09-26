#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(tr -d '[:space:]' < "$ROOT_DIR/VERSION")"
api_version="$(sed -n 's/^version = "\([^"]*\)"$/\1/p' "$ROOT_DIR/api/pyproject.toml" | head -n1)"
ui_version="$(sed -n 's/^  "version": "\([^"]*\)",$/\1/p' "$ROOT_DIR/ui/package.json" | head -n1)"
lock_version="$(sed -n '3s/^  "version": "\([^"]*\)",$/\1/p' "$ROOT_DIR/ui/package-lock.json")"
lock_package_version="$(sed -n '9s/^      "version": "\([^"]*\)",$/\1/p' "$ROOT_DIR/ui/package-lock.json")"

for value in "$version" "$api_version" "$ui_version" "$lock_version" "$lock_package_version"; do
  [[ "$value" == "$version" ]] || {
    echo "Version mismatch: VERSION=$version api=$api_version ui=$ui_version lock=$lock_version lock-package=$lock_package_version" >&2
    exit 1
  }
done

echo "$version"
