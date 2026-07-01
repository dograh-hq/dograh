# Sativoice Enterprise — Rebrand Design

**Date:** 2026-07-01
**Status:** Approved
**Author:** andreab

## Context

Dograh is an open-source voice AI platform (BSD 2-Clause) — a self-hostable alternative to Vapi & Retell. The name "Dograh" is problematic in the Italian market: it sounds foreign, evokes "droga" (drugs) to Italian ears, and is not sellable to corporate clients.

Goal: create an Italian market brand — **Sativoice Enterprise** (under Satisfactory Group) — while maintaining full compatibility with upstream Dograh (`TopCS/dograh` on GitHub).

Working branch: `main` (fork). Upstream updates are merged periodically from `upstream/main`.

### Git Remote Setup (Prerequisite)

```bash
# After forking TopCS/dograh on GitHub:
git clone <your-fork-url> sativoice
cd sativoice
git remote add upstream git@github.com:TopCS/dograh.git
git remote -v  # Verify: origin = your fork, upstream = TopCS/dograh
```

## Target Audience

PMI and corporate Italian companies looking for voice AI solutions (call centers, customer service automation, telephony AI). The brand must sound professional, trustworthy, and Italian/Latin in style.

## Rebrand Strategy: Approach A — Surface-Only

### What changes

| Layer | Changes? | Detail |
|---|---|---|
| `README.md` | ✅ Yes | Title → "Sativoice Enterprise", description, links, badges, comparison table |
| `README.it-IT.md` | 🆕 New | Italian README for corporate clients |
| `README.zh-CN.md`, `README.ja-JP.md` | ❌ Skipped | Left as upstream — irrelevant for Italian market |
| `CONTRIBUTING.md` | ✅ Yes | Project name references |
| `SECURITY.md` | ✅ Yes | Project name references |
| `CHANGELOG.md` | ✅ Yes | Project name references |
| `PRIVATE_DEPLOYMENT_PLAN.md` | ✅ Yes | Project name references |
| `AGENTS.md` | ✅ Yes | Project name references |
| `release-please-config.json` | ✅ Yes | Release PR title/prefix |
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
| Package names (`dograh_sdk`, `@dograh/sdk`) | ❌ Unchanged | Would break all imports. Acknowledged trade-off: SDK users will see `@dograh/sdk` in their `package.json`. Acceptable because SDK consumers are technical and understand the fork relationship. |
| UI components (`DograhCreditsCard.tsx`, ...) | ❌ Unchanged | Internal — not customer-facing |
| All `.py` / `.ts` / `.tsx` internals | ❌ Unchanged | Upstream compatibility |
| `.github/workflows/docker-image.yml` | ❌ Unchanged | Determines actual registry path. `docker-compose` image tags are cosmetic — CI pushes to whatever registry is configured. |
| URLs (`app.dograh.com`, `docs.dograh.com`) | Out of scope | Handled at DNS/cloud level |

### Guiding principle

The end customer (and anyone deploying) sees "Sativoice". Anyone reading the source code sees "Dograh" — and that's fine, because it's open-source based on Dograh.

## Upstream Sync Strategy: Idempotent Script

A script `scripts/rebrand-to-sativoice.sh` applies all `Dograh` → `Sativoice` substitutions to the surface files. It is **idempotent**: safe to run multiple times, only touches files that still contain "Dograh".

### Workflow after upstream pull

```bash
git fetch upstream
git merge upstream/main
./scripts/rebrand-to-sativoice.sh
# Script prints verification: "✅ N Dograh references remaining in surface files"
git commit -am "chore: sync upstream + rebrand"
```

### Verification (built into script)

After applying substitutions, the script runs:

```bash
grep -r "Dograh" <surface-files> || echo "✅ No Dograh references found in surface files"
```

This makes the idempotency guarantee verifiable — re-running the script should always reach the same clean state.

### Docker image registry note

`docker-compose.yaml` `image:` tags are changed to `sativoice/` for cosmetic consistency, but actual image builds are driven by `.github/workflows/docker-image.yml`. That workflow remains upstream-identical to avoid CI breakage. The compose labels are for the deployer's benefit, not the registry.

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

1. `scripts/rebrand-to-sativoice.sh` — idempotent rebrand script with verification step
2. `ui/public/sativoice-logo.svg` (or replacement logo asset)
3. `README.it-IT.md` — Italian README for corporate clients

### Modified files (~25 total)

See "What changes" table above. All modifications are string-level replacements of "Dograh" → "Sativoice" in specific, scoped files only.

## Custom Modules (Post-Rebrand Milestones)

Three custom modules will be developed within the fork, leveraging the existing integration plugin architecture to avoid merge conflicts with upstream.

### Modules

| Module | Location | Pattern | Merge Risk |
|---|---|---|---|
| FS Integration | `api/services/filesystem/` (extend existing providers) | Core extension — new storage backend | Low (new files in existing package) |
| Web Widget (voice + text) | `widgets/sativoice-web-widget/` | Standalone npm package in monorepo, CDN-deployable | Zero (new top-level directory) |
| WhatsApp Gupshup | `api/services/integrations/gupShup/` | Integration plugin — auto-discovered by loader | Zero (new directory, upstream won't have it) |

### Why in the fork, not separate repos

- All three modules have tight coupling with core (DB models, workflow engine, auth system)
- The integration plugin system (`api/services/integrations/loader.py`) auto-discovers new directories — upstream can't conflict with what it doesn't have
- Separate repos would require cross-repo versioning, complex integration tests, and worse DX

### Upstream sync with custom modules

```bash
git fetch upstream
git merge upstream/main          # No conflicts: our modules are in new directories
./scripts/rebrand-to-sativoice.sh
pytest api/tests/ -k "gupShup or fs_integration"  # Verify custom modules still work
git commit -am "chore: sync upstream + rebrand"
```

**Merge conflict risk:** near zero. Custom code lives in directories upstream doesn't own.
**Real risk:** API breakage — if upstream changes `IntegrationPackageSpec` or `IntegrationRuntimeSession` interfaces. Caught by tests, not by merge conflicts.

## Out of Scope

- Renaming the GitHub repository or organization
- Changing `app.dograh.com` or `docs.dograh.com` domains
- Publishing separate SDK packages
- Renaming internal Python/TypeScript classes
- Changing environment variable names (`DOGRAH_*`)
- Modifying CI/CD release workflows
- Changing the LICENSE file
