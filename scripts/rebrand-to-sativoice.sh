#!/usr/bin/env bash
# rebrand-to-sativoice.sh — Idempotent Dograh → Sativoice rebrand for surface files.
# Safe to run multiple times. Only touches files that still contain "Dograh" or "dograh".
# Run after `git merge upstream/main` to re-apply branding.
set -euo pipefail

SURFACE_FILES=(
    "README.md"
    "CONTRIBUTING.md"
    "SECURITY.md"
    "CHANGELOG.md"
    "PRIVATE_DEPLOYMENT_PLAN.md"
    "AGENTS.md"
    "release-please-config.json"
    "docker-compose.yaml"
    "docker-compose-local.yaml"
    "api/Dockerfile"
    "ui/Dockerfile"
    # Scripts safe for blanket sed (user-facing messages only, no code identifiers)
    "scripts/start_docker.sh"
    "scripts/start_docker.ps1"
    "scripts/remote_up.sh"
    "scripts/setup_fork.sh"
    "scripts/setup_fork.ps1"
    "scripts/generate_sdk.sh"
    "scripts/release_sdks.sh"
    "scripts/setup_pipecat.sh"
    "scripts/setup_requirements.sh"
    "scripts/setup_requirements.ps1"
    # Scripts EXCLUDED: setup_local.sh, setup_local.ps1, setup_remote.sh,
    #   update_remote.sh, rolling_update.sh, run_dograh_init.sh,
    #   lib/setup_common.sh — these contain code-level identifiers
    #   (file paths, function names, string comparisons, HTTP headers)
    #   that must NOT be blanket-sed'd.
    "ui/src/app/layout.tsx"
    "docs/docs.json"
    "docs/api-reference/openapi.json"
)

echo "==> Sativoice rebrand — $(date)"
changed=0
skipped=0

for file in "${SURFACE_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo "SKIP (missing): $file"
        ((skipped++)) || true
        continue
    fi

    if ! grep -q -E "Dograh|dograh" "$file" 2>/dev/null; then
        echo "SKIP (clean):  $file"
        ((skipped++)) || true
        continue
    fi

    cp "$file" "$file.bak"
    sed -i 's/Dograh/Sativoice/g' "$file"
    sed -i 's/dograh/sativoice/g' "$file"
    echo "DONE:          $file"
    ((changed++)) || true
done

echo ""
echo "==> Summary: $changed files changed, $skipped files skipped"

# Revert file-path references that are code identifiers, not surface text
# The blanket sed above converts "run_dograh_init.sh" → "run_sativoice_init.sh"
# but the actual file on disk is still named run_dograh_init.sh.
sed -i 's|run_sativoice_init\.sh|run_dograh_init.sh|g' docker-compose.yaml 2>/dev/null || true

# Verification: count remaining [Dd]ograh references in surface files
# Matches what the sed commands handle: "Dograh" and "dograh", not all-caps DOGRAH_
echo ""
echo "==> Verification:"
REMAINING=0
for file in "${SURFACE_FILES[@]}"; do
    if [ -f "$file" ]; then
        count=$(grep -c "[Dd]ograh" "$file" 2>/dev/null || true)
        REMAINING=$((REMAINING + count))
    fi
done

if [ "$REMAINING" -eq 0 ]; then
    echo "✅ No [Dd]ograh references found in surface files"
else
    echo "⚠️  $REMAINING [Dd]ograh references remaining in surface files:"
    for file in "${SURFACE_FILES[@]}"; do
        [ -f "$file" ] && grep -Hn "[Dd]ograh" "$file" 2>/dev/null || true
    done
fi
