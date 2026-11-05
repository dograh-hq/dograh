#!/usr/bin/env bash
# Pull dograh-hq/dograh into the fork with minimal conflicts.
# Branding is NOT in the source tree (see branding/), so a merge should be clean.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream https://github.com/dograh-hq/dograh
git config rerere.enabled true           # remember conflict resolutions
git config merge.ours.driver true        # honour merge=ours in .gitattributes
git fetch upstream
git merge --no-edit upstream/main || { echo "Resolve conflicts, commit; rerere will remember them."; exit 1; }
git submodule update --init --recursive
python3 branding/apply.py --dry-run >/dev/null || echo "Review overrides: python3 branding/apply.py --dry-run, then --update-lock"
python3 branding/apply.py --check | tail -1
