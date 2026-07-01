# Sativoice Enterprise Rebrand — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all public-facing "Dograh" references with "Sativoice" across ~25 files, create a reusable idempotent rebrand script, and add Italian README + placeholder logo.

**Architecture:** A single bash script (`scripts/rebrand-to-sativoice.sh`) handles all sed-based substitutions. The script is idempotent (safe to re-run). Two manual tasks cover new files (README.it-IT.md, logo) and context-specific changes (layout.tsx metadata object). The script itself is the deliverable — future upstream syncs use it.

**Tech Stack:** Bash (sed, grep), TypeScript (layout.tsx metadata), Markdown (README.it-IT.md), SVG (logo placeholder)

## Global Constraints

- All string replacements are `Dograh` → `Sativoice`, `dograh` → `sativoice` (case-sensitive as appropriate)
- Script must be idempotent: running twice produces no net changes
- Script must guard against missing files (upstream may delete/rename)
- Do NOT touch: `README.zh-CN.md`, `README.ja-JP.md`, env vars (`DOGRAH_*`), Python/TS class names, SDK packages, `.py`/`.ts` internals
- UI metadata title: "Dograh" → "Sativoice Enterprise", description: "Open Source Voice Assistant Workflow Builder" → "Piattaforma Voice AI Enterprise per il mercato italiano"

---

### Task 1: Create the idempotent rebrand script

**Files:**
- Create: `scripts/rebrand-to-sativoice.sh`

**Interfaces:**
- Produces: `scripts/rebrand-to-sativoice.sh` — executable bash script, idempotent

- [ ] **Step 1: Write the rebrand script**

```bash
#!/usr/bin/env bash
# rebrand-to-sativoice.sh — Idempotent Dograh → Sativoice rebrand for surface files.
# Safe to run multiple times. Only touches files that still contain "Dograh".
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
    "scripts/start_docker.sh"
    "scripts/start_docker.ps1"
    "scripts/setup_local.sh"
    "scripts/setup_local.ps1"
    "scripts/setup_remote.sh"
    "scripts/remote_up.sh"
    "scripts/setup_fork.sh"
    "scripts/setup_fork.ps1"
    "scripts/update_remote.sh"
    "scripts/setup_requirements.sh"
    "scripts/setup_requirements.ps1"
    "scripts/rolling_update.sh"
    "scripts/run_dograh_init.sh"
    "scripts/generate_sdk.sh"
    "scripts/release_sdks.sh"
    "scripts/setup_custom_domain.sh"
    "scripts/setup_pipecat.sh"
    "scripts/start_services.sh"
    "scripts/lib/setup_common.sh"
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

    if ! grep -q "Dograh" "$file" 2>/dev/null; then
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

# Verification: count remaining Dograh references in surface files
echo ""
echo "==> Verification:"
REMAINING=$(for file in "${SURFACE_FILES[@]}"; do
    [ -f "$file" ] && cat "$file" 2>/dev/null
done | grep -c "Dograh" 2>/dev/null || echo 0)

if [ "$REMAINING" -eq 0 ]; then
    echo "✅ No Dograh references found in surface files"
else
    echo "⚠️  $REMAINING Dograh references remaining in surface files:"
    for file in "${SURFACE_FILES[@]}"; do
        [ -f "$file" ] && grep -Hn "Dograh" "$file" 2>/dev/null || true
    done
fi
```

- [ ] **Step 2: Make the script executable**

```bash
chmod +x scripts/rebrand-to-sativoice.sh
```

- [ ] **Step 3: Run the script for the first time**

```bash
./scripts/rebrand-to-sativoice.sh
```

Expected: lists each changed file with "DONE", ends with "✅ No Dograh references found"

- [ ] **Step 4: Verify idempotency — run again**

```bash
./scripts/rebrand-to-sativoice.sh
```

Expected: every file shows "SKIP (clean)", summary shows `0 files changed`

- [ ] **Step 5: Commit**

```bash
git add scripts/rebrand-to-sativoice.sh
git add $(for f in "${SURFACE_FILES[@]}"; do [ -f "$f" ] && echo "$f"; done) 2>/dev/null
git commit -m "feat: Sativoice Enterprise rebrand — surface files + idempotent script"
```

---

### Task 2: Italian README and UI metadata polish

**Files:**
- Create: `README.it-IT.md`
- Modify: `ui/src/app/layout.tsx` (metadata object needs custom description, not just sed)

**Interfaces:**
- Consumes: `scripts/rebrand-to-sativoice.sh` (Task 1) has already run `sed` on `layout.tsx`
- Produces: `README.it-IT.md` (Italian), `ui/src/app/layout.tsx` (corrected metadata)

- [ ] **Step 1: Create README.it-IT.md**

```markdown
# Sativoice Enterprise

**Piattaforma Voice AI open-source per il mercato enterprise italiano** —
costruita su [Dograh](https://github.com/TopCS/dograh), l'alternativa
self-hostabile a Vapi e Retell.

Sativoice Enterprise è la distribuzione italiana ufficiale, mantenuta da
**Satisfactory Group**. Offriamo:

- **100% open source** (BSD 2-Clause) — nessun vendor lock-in
- **Self-hosting on-premise** — i tuoi dati restano nella tua infrastruttura
- **GDPR-ready** — deployment su suolo italiano, dati sotto il tuo controllo
- **Supporto enterprise** — SLA, formazione, personalizzazioni

## 🚀 Per iniziare

Vedi la [guida al deploy](https://docs.sativoice.com) per installare
Sativoice Enterprise nel tuo ambiente.

## 📞 Contatti

- **Satisfactory Group** — [sativoice.com](https://sativoice.com)
- **Documentazione** — [docs.sativoice.com](https://docs.sativoice.com)
- **Email** — enterprise@sativoice.com
```

- [ ] **Step 2: Fix layout.tsx metadata** — the sed script changed `"Dograh"` → `"Sativoice"` in the title, but the description needs updating too

Open `ui/src/app/layout.tsx` and verify the metadata block reads:

```typescript
export const metadata: Metadata = {
  title: "Sativoice Enterprise",
  description: "Piattaforma Voice AI Enterprise per il mercato italiano",
};
```

If the sed script left `"Open Source Voice Assistant Workflow Builder"`, replace it manually.

- [ ] **Step 3: Commit**

```bash
git add README.it-IT.md ui/src/app/layout.tsx
git commit -m "feat: Italian README + UI metadata for Sativoice Enterprise"
```

---

### Task 3: Placeholder logo asset

**Files:**
- Create: `ui/public/sativoice-logo.svg`

**Interfaces:**
- Produces: `ui/public/sativoice-logo.svg` — minimal SVG placeholder, ready to replace with final design

- [ ] **Step 1: Create placeholder logo**

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 40" width="200" height="40">
  <text x="0" y="28" font-family="system-ui, sans-serif" font-size="24" font-weight="700" fill="#2563eb">
    Sativoice
  </text>
</svg>
```

- [ ] **Step 2: Commit**

```bash
git add ui/public/sativoice-logo.svg
git commit -m "feat: placeholder Sativoice logo asset"
```

---

### Task 4: Final verification

**Files:**
- Verify: all surface files are clean

- [ ] **Step 1: Re-run rebrand script (idempotency check)**

```bash
./scripts/rebrand-to-sativoice.sh
```

Expected: `0 files changed`, `✅ No Dograh references found`

- [ ] **Step 2: Spot-check key files**

```bash
grep -H "Dograh\|dograh" README.md docker-compose.yaml ui/src/app/layout.tsx || echo "✅ Clean"
```

- [ ] **Step 3: Verify internal code is untouched**

```bash
# These must still contain Dograh (class names, SDK)
grep -c "DograhClient" api/services/configuration/registry.py
grep -c "DograhClient" sdk/python/src/dograh_sdk/client.py
# Should output: 1 or more — classes are unchanged
```

- [ ] **Step 4: Final commit if any residual changes**

```bash
git status
git diff --stat
# If clean: done. If residual changes exist, review and commit.
```
