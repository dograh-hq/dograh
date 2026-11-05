# Donna white-label overlay

Goal: the source tree stays byte-identical to `dograh-hq/dograh` so `git merge upstream/main`
never conflicts on branding. All rebranding is applied at **build time** by `apply.py`
(CI runs it before every Docker build; it is never committed).

| What | Where |
|---|---|
| Name, URL removals, text/regex rules | `brand.json` |
| Logos / favicon | `assets/` (mapped to upstream paths in `brand.json`) |
| Whole-file replacements (Footer, GitHub badge) | `overrides/` — `overrides.lock.json` warns if upstream changes the file |
| Runtime values (legal URLs, API URLs) | env vars, e.g. `NEXT_PUBLIC_PRIVACY_URL`, `NEXT_PUBLIC_TERMS_URL` |

Commands: `python3 branding/apply.py [--dry-run|--check|--update-lock]`, `scripts/sync_upstream.sh`.
Local preview: run `apply.py`, then `git checkout -- . && git clean -fd ui api` to undo
(or run it in a throwaway worktree).

After each upstream merge: run `--check` for new leftovers; if apply.py exits non-zero, an
overridden file changed upstream — diff it, update the override, run `--update-lock`.
Don't rename Python packages, DB values, API enums or the `dograh` provider mode: those are
identifiers, not branding, and renaming them recreates the conflicts.
