# Sativoice Enterprise — Rebrand Design

**Date:** 2026-07-01
**Status:** Approved
**Author:** andreab

## Context

Dograh is an open-source voice AI platform (BSD 2-Clause) — a self-hostable alternative to Vapi & Retell. The name "Dograh" is problematic in the Italian market: it sounds foreign, evokes "droga" (drugs) to Italian ears, and is not sellable to corporate clients.

Goal: create an Italian market brand — **Sativoice Enterprise** (under Satisfactory Group) — while maintaining full compatibility with upstream Dograh (`TopCS/dograh` on GitHub).

## Target Audience

PMI and corporate Italian companies looking for voice AI solutions (call centers, customer service automation, telephony AI). The brand must sound professional, trustworthy, and Italian/Latin in style.

## Rebrand Strategy: Approach A — Surface-Only

### What changes

| Layer | Changes? | Detail |
|---|---|---|
| `README.md`, `README.zh-CN.md`, `README.ja-JP.md` | ✅ Yes | Title → "Sativoice Enterprise", description, links, badges |
| `docker-compose.yaml`, `docker-compose-local.yaml` | ✅ Yes | `image:` tags → `sativoice/`, service labels, comments |
| `api/Dockerfile`, `ui/Dockerfile` | ✅ Yes | Labels, comments |
| `scripts/*.sh`, `scripts/*.ps1` | ✅ Yes | Echo messages, printed names shown to the user |
| `scripts/lib/setup_common.sh` | ✅ Yes | Log/echo messages |
| `ui/src/app/layout.tsx` | ✅ Yes | `<title>`, metadata |
| `ui/public/` | ✅ Yes | Favicon, logo assets |
| `docs/docs.json` | ✅ Yes | Project name field |
| `docs/api-reference/openapi.json` | ✅ Yes | `info.title` |

### What stays

| Layer | Stays | Reason |
|---|---|---|
| Env vars (`DOGRAH_*`) | ❌ Unchanged | Internal — not customer-facing |
| Python classes (`DograhClient`, `DograhLLMService`, ...) | ❌ Unchanged | Upstream compatibility |
| TypeScript classes (`DograhClient`, `DograhDefaults`, ...) | ❌ Unchanged | Upstream compatibility |
| Package names (`dograh_sdk`, `@dograh/sdk`) | ❌ Unchanged | Would break all imports |
| UI components (`DograhCreditsCard.tsx`, ...) | ❌ Unchanged | Internal — not customer-facing |
| All `.py` / `.ts` / `.tsx` internals | ❌ Unchanged | Upstream compatibility |
| CI/CD workflows (`.github/workflows/`) | ❌ Unchanged | Must track upstream |
| URLs (`app.dograh.com`, `docs.dograh.com`) | Out of scope | Handled at DNS/cloud level |

### Guiding principle

The end customer (and anyone deploying) sees "Sativoice". Anyone reading the source code sees "Dograh" — and that's fine, because it's open-source based on Dograh.

## Upstream Sync Strategy: Idempotent Script

A script `scripts/rebrand-to-sativoice.sh` applies all `Dograh` → `Sativoice` substitutions to the surface files. It is **idempotent**: safe to run multiple times, only touches files that still contain "Dograh".

### Workflow after upstream pull

```bash
git pull upstream main
./scripts/rebrand-to-sativoice.sh
git commit -am "chore: re-apply Sativoice branding"
```

### Script design

```bash
#!/usr/bin/env bash
set -euo pipefail

# Each transformation:
#   1. Checks if the file exists (guards against upstream deletions)
#   2. Checks if "Dograh" is present (guards against redundant edits)
#   3. Applies targeted sed replacements
#
# Files touched:
#   - README.md, README.zh-CN.md, README.ja-JP.md
#   - docker-compose.yaml, docker-compose-local.yaml
#   - api/Dockerfile, ui/Dockerfile
#   - scripts/*.sh, scripts/*.ps1, scripts/lib/*.sh
#   - ui/src/app/layout.tsx
#   - docs/docs.json, docs/api-reference/openapi.json
```

### Why script over branch-merge or patch

- **Script wins over branch-merge:** No merge conflicts when upstream touches README or docker-compose. Just re-run the script.
- **Script wins over .patch:** Resilient to file moves/renames. A grep-based script handles structural changes gracefully; a patch file does not.

## Files to Create/Modify

### New files

1. `scripts/rebrand-to-sativoice.sh` — idempotent rebrand script
2. `ui/public/sativoice-logo.svg` (or replacement logo asset)

### Modified files (~20 total)

See "What changes" table above. All modifications are string-level replacements of "Dograh" → "Sativoice" in specific, scoped files only.

## Out of Scope

- Renaming the GitHub repository or organization
- Changing `app.dograh.com` or `docs.dograh.com` domains
- Publishing separate SDK packages
- Renaming internal Python/TypeScript classes
- Changing environment variable names (`DOGRAH_*`)
- Modifying CI/CD release workflows
- Changing the LICENSE file
