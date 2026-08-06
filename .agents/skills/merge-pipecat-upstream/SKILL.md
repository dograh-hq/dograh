---
name: merge-pipecat-upstream
description: Merge the latest upstream pipecat-ai/pipecat tag into the pipecat submodule fork (dograh-hq/pipecat) and bump the dograh repo to it. Use whenever the user asks to bump, upgrade, sync, or merge pipecat, resolve pipecat merge conflicts, audit whether upstream changes break or supersede Dograh's in-fork patches, or verify the api/ wrapper subclasses still match the upstream classes they wrap.
---

# Merging upstream pipecat

`pipecat/` is a git submodule of the fork `dograh-hq/pipecat` (`origin`), installed editable by `scripts/setup_requirements.sh`. Upstream is `https://github.com/pipecat-ai/pipecat` and is usually not configured as a remote. Fork `main` is the integration branch; the dograh repo pins a commit of it via the submodule pointer.

Dograh customization lives in two layers, and both must be audited on every merge:

1. **In-fork changes** — dograh-owned modules that don't exist upstream (`src/pipecat/services/dograh/`, serializers like `vobiz.py`/`cloudonix.py`/`asterisk.py`, `call_strategies.py`, `tests/test_dograh_services.py`) plus **patches to upstream files** (aggregators, transports, turn tracking, serializers).
2. **api/-side wrappers** — subclasses in the dograh repo (`api/services/pipecat/`, `api/services/telephony/providers/*/`, `api/services/workflow/pipecat_engine*.py`) that override upstream hooks and reach into private state (`self._session`, `self._bot_is_responding`, …). These break **silently** on upstream refactors — no merge conflict, no import error.

Freshness rule: trust the current repos over any inventory in this file. Discover state with the commands below; don't assume file lists here are complete.

## 1. Baseline and setup

```bash
cd pipecat
git log --first-parent --oneline --merges | grep -m1 "Merge tag"   # OLD_TAG = last merged upstream tag
git remote add upstream https://github.com/pipecat-ai/pipecat.git 2>/dev/null
git fetch upstream --tags
git tag --sort=-v:refname | head -5                                # pick NEW_TAG (latest stable)
git checkout -b merge-vNEW origin/main
```

In the dograh repo, work on a `bump-pipecat-X.Y` branch.

## 2. Recon before merging

Do this **before** `git merge` — conflict resolution decisions must be made from evidence, not on the fly.

```bash
git diff NEW_TAG...origin/main --stat -- src tests    # full surviving dograh delta (three-dot = from merge-base)
git log --first-parent --oneline OLD_MERGE_COMMIT..origin/main   # dograh commits since last merge
```

Classify each dograh-touched file:

- **Dograh-owned** (`git cat-file -e NEW_TAG:<path>` fails → file doesn't exist upstream): merges clean, but must be adapted to new upstream contracts afterwards.
- **Patched upstream file**: the risk zone. For each, get upstream's changes in the range and decide a verdict *per patch*:
  ```bash
  git log --oneline OLD_TAG..NEW_TAG -- <path>
  git diff OLD_TAG NEW_TAG -- <path>
  ```
  - **keep ours** — upstream didn't touch the patched logic
  - **take theirs** — upstream fixed the same problem; carrying the dograh patch would be redundant or fight upstream's fix
  - **rework** — both changed; port the dograh intent onto the new upstream code

Read `CHANGELOG.md` for the OLD_TAG..NEW_TAG range — it names breaking changes and deprecations that the diff alone obscures.

## 3. Merge

```bash
git merge NEW_TAG
```

Resolve conflicts using the recon verdicts. Then adapt dograh-owned modules to changed upstream contracts (base-class signatures, renamed frames/params) — precedent: the v1.5.0 merge needed a follow-up "Fix Dograh services for Pipecat 1.5 contracts" touching `src/pipecat/services/dograh/` and `tests/test_dograh_services.py`.

## 4. Audit the Dograh MPS services (fork side)

The services in `src/pipecat/services/dograh/` are thin clients for model services (`~/Projects/dograh/model_services`). The wire protocol is fixed by the separately-deployed server; the pipecat-facing half must track upstream base contracts. For each service, diff its base classes OLD_TAG..NEW_TAG and verify every wire message still fires at the same conversational boundary — STT finalization on end of user speech, TTS context open/close/cancel across turn end and interruption, LLM billing metadata on every request. These couplings live in lifecycle hooks and private base state, so they drift silently rather than error. Answer server-behavior questions from model_services source; never change wire verbs without a coordinated model_services change.

## 5. Audit the api/ wrappers

Inventory the wrapper surface (in the dograh repo):

```bash
rg -n "class Dograh\w+\(" api --type py                       # named wrappers
rg -l "^from pipecat" api/services api/utils --type py        # full consumer surface
```

For each subclass of a pipecat class (realtime services, `service_factory.py` LLM subclasses, `minimax_tts.py`, FrameProcessors, observers, telephony transports/serializers/strategies):

1. Diff the wrapped upstream class: `git -C pipecat diff OLD_TAG NEW_TAG -- <upstream file>`. Unchanged file → wrapper is fine, move on.
2. If changed, verify every **overridden method** still exists upstream with the same call semantics (when it's called, what `super()` now does, sync vs async).
3. Verify every **base-class private attr** the wrapper reads or writes still exists with the same meaning:
   ```bash
   rg -o "self\._\w+" <wrapper.py> | sort -u   # then check names not defined in the wrapper against NEW_TAG's class
   ```
4. Check **redundancy/conflict**: upstream absorbs Dograh behavior over time (mute gating, deferred function calls, reconnect handling). If the new upstream class now does what the override does, the wrapper duplicates it (double-firing) or fights it (two competing code paths) — trim the override instead of stacking behavior.

Also check non-subclass consumers: `pipecat_engine*.py` and frame/type imports across `api/` — removed or renamed symbols surface only at import time.

## 6. Validate

```bash
./scripts/setup_requirements.sh        # reinstall; first check its hardcoded extras list still matches upstream pyproject extras
source venv/bin/activate && python -c "import api.app"   # import smoke test
cd pipecat && python -m pytest tests/test_dograh_services.py
```

Run api tests with `set -a && source api/.env.test && set +a`: first the tests matching audited wrappers (e.g. `api/tests/test_azure_realtime_wrapper.py`) and `api/tests/telephony/` serializer tests for fast iteration, then the full `api/tests/` suite as the final check.

Never rebase fork `main` — old dograh-repo commits reference its commits via submodule pointers; history must stay append-only.
