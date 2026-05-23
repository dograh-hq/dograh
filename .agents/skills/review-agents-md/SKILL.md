---
name: review-agents-md
description: Audit Dograh `AGENTS.md` files for drift against the live repo and for bad scope boundaries between parent and child docs. Use when the user asks to review existing AGENTS files, identify stale guidance, decide whether a subtree needs its own `AGENTS.md`, or update the `AGENTS.md` hierarchy under the repo root, `api/`, or `ui/`.
---

# Review AGENTS.md

Audit first. Report drift, missing coverage, and wrong ownership boundaries before editing docs unless the user explicitly asks for patches.

## Freshness Rule

Treat the repo as source of truth.

- Trust current code and current directory layout over any `AGENTS.md`, `README.md`, or this skill's references.
- If prose and code disagree, report the prose as stale.
- If a reference file in this skill disagrees with the repo, trust the repo and mention the drift.

## Workflow

### 0. Refresh the seam reference before using it

If subagents are available, refresh `references/dograh-seams.md` before relying on it.

- Spawn exactly one subagent for this maintenance pass.
- Tell the subagent to inspect the live repo.
- Limit its patch set to `.agents/skills/review-agents-md/references/dograh-seams.md`.
- Also allow `.agents/skills/review-agents-md/scripts/inventory_agents_md.py`, but only if the helper itself needs a repo-specific fix.
- Tell the subagent not to recurse into this same seam-refresh workflow. This is a one-level maintenance pass, not an infinite self-audit loop.
- Tell the subagent not to review or patch any repo `AGENTS.md` files yet. Its job is only to refresh the seam reference and helper.
- After the subagent returns, review its diff quickly before using `dograh-seams.md` in the main audit.

Use a prompt shaped like:

```text
Review and refresh .agents/skills/review-agents-md/references/dograh-seams.md against the live Dograh repo. Patch only that file, and patch .agents/skills/review-agents-md/scripts/inventory_agents_md.py only if needed. Do not recurse into another seam-refresh pass. Do not review or edit any AGENTS.md files yet.
```

If subagents are not available, do the same seam refresh locally before continuing.

### 1. Inventory the current hierarchy

Run the helper first from the repo root:

```bash
python .agents/skills/review-agents-md/scripts/inventory_agents_md.py
```

This prints:

- every discovered `AGENTS.md`
- child `AGENTS.md` ownership boundaries
- immediate child directories for each scope
- large uncovered subtrees that may deserve their own `AGENTS.md`

Then confirm with direct file discovery when needed:

```bash
rg --files -g 'AGENTS.md' .
find api ui -name AGENTS.md | sort
```

### 2. Read top-down before judging details

Read in this order:

1. repo root `AGENTS.md`
2. `api/AGENTS.md`
3. `ui/AGENTS.md`
4. deeper `AGENTS.md` files under those trees

For each file, write a one-line ownership statement in your notes:

- what subtree it owns
- what shared rules it should contain
- which deeper docs, if any, should own implementation details instead

### 3. Verify each doc against the live code

Check directory trees, route aggregators, registration points, and extension seams instead of relying on filenames mentioned in prose.

Dograh files worth checking early:

- `api/routes/main.py`
- `api/routes/telephony.py`
- `api/services/integrations/loader.py`
- `api/services/integrations/registry.py`
- `api/services/pipecat/run_pipeline.py`
- `api/tasks/run_integrations.py`
- `api/services/telephony/registry.py`
- `api/services/telephony/factory.py`
- `api/services/telephony/providers/__init__.py`
- `ui/src/app/`
- `ui/src/components/`
- `ui/src/lib/auth/`
- `ui/src/client/`

Read [dograh-seams.md](references/dograh-seams.md) when you need a fast repo-specific starting map.

### 4. Apply the hierarchy tests

Use these tests for every scope:

- `parent-fit`: The parent doc explains immediate child systems, shared invariants, and navigation for the subtree it owns.
- `child-fit`: A deeper doc owns local extension contracts, module-specific gotchas, and file-level patterns for its own subtree.
- `no-duplication`: The parent does not restate detailed child implementation guidance that should live in the child doc.
- `downward-pointing`: The doc should point contributors toward the next relevant subdirectory or deeper `AGENTS.md` instead of trying to explain the whole subtree itself.
- `no-gaps`: If a large or extension-heavy subtree has rules the parent cannot explain cleanly in a few lines, flag a missing child `AGENTS.md`.
- `no-drift`: File trees, commands, extension points, and architecture claims still match the code.

### 5. Dograh-specific review heuristics

#### Root `AGENTS.md`

Expect root to stay high-level.

- It should describe the top-level project shape, shared stack, and shared local-development expectations.
- It should mention top-level applications and support directories that matter to contributors.
- It should not try to document backend-internal extension contracts or frontend component internals.
- If a top-level directory materially matters to contributors and is missing from root guidance, report `missing-parent-coverage`.

#### `api/AGENTS.md`

Expect `api/AGENTS.md` to orient a contributor across backend domains, not to document every local contract in full.

- It should point to where routes, services, DB access, schemas, tasks, tests, and security invariants live.
- It should accurately describe where workflow execution lives. In current Dograh, that spans `api/services/workflow/`, `api/services/pipecat/`, and post-call work in `api/tasks/run_integrations.py`.
- It should accurately describe telephony as a substantial subsystem, not just as one route file.
- It should mention integration extensibility and defer package-level rules to `api/services/integrations/AGENTS.md`.
- If `api/services/telephony/` or `api/services/workflow/` are complex enough that the parent doc becomes vague or overloaded, report `missing-child-agents`.

#### `api/services/integrations/AGENTS.md`

Expect this file to own the integration package contract.

- It should explain package registration, node model/spec patterns, runtime collection, completion handlers, optional routes, import discipline, and testing expectations.
- It should match the live registry/loader path instead of describing central manual wiring that no longer exists.
- It should not require edits to `workflow/dto.py`, `run_pipeline.py`, or route aggregation unless the generic framework genuinely changed.

#### `ui/AGENTS.md`

Expect `ui/AGENTS.md` to orient contributors across the frontend without documenting individual feature internals.

- It should describe the App Router layout under `ui/src/app/`.
- It should point to reusable feature components under `ui/src/components/`.
- It should mention generated API client usage under `ui/src/client/`.
- It should mention auth readiness constraints under `ui/src/lib/auth/`.
- It should not describe removed folders or outdated stack details.

### 6. Classify findings

Use these categories:

- `stale`: prose mentions files, commands, flows, or architecture that no longer match the repo
- `missing-parent-coverage`: a parent scope omits a major subsystem it should orient the reader to
- `missing-child-agents`: a deep subtree likely needs its own `AGENTS.md`
- `wrong-level`: content belongs in a parent or child scope instead
- `extra-detail`: a parent doc is too implementation-specific for its level

### 7. Report format

List findings first, ordered by severity.

Use this shape:

```text
<path>: <category> -> <problem> -> <what should own or replace it>
```

Examples:

```text
api/AGENTS.md: missing-child-agents -> telephony is a large extension surface with provider registration, transport, routes, and config rules but has no local AGENTS.md -> add api/services/telephony/AGENTS.md and keep api/AGENTS.md at navigation level
api/services/integrations/AGENTS.md: stale -> says central DTO edits are required for new integrations, but registry-based discovery handles node resolution -> update the doc to describe the registry path only
ui/AGENTS.md: wrong-level -> describes individual workflow-builder component behavior instead of frontend navigation rules -> move that detail to a deeper doc or remove it
```

After findings, include:

- open questions or assumptions
- optional patch plan, only if the user asked for fixes or clearly wants them next

## Editing Rules

If the user wants the docs fixed:

- patch the smallest set of `AGENTS.md` files that restores a clean hierarchy
- add a new `AGENTS.md` only when a subtree has distinct local rules or extension contracts
- keep parent docs short and navigational
- let child docs own local implementation rules
- avoid copying the same guidance into parent and child files
- prefer folders over files when writing navigation guidance, unless a file is the only real seam
- when adding a new child `AGENTS.md`, start with the shortest useful contract; avoid tutorial-style prose
- point the reader downward toward the next relevant subdirectory or child `AGENTS.md`
- if a draft feels forced or over-explained, compress it again

## Useful Commands

```bash
python .agents/skills/review-agents-md/scripts/inventory_agents_md.py
rg --files -g 'AGENTS.md' .
find api ui -name AGENTS.md | sort
find api/services -maxdepth 2 -type d | sort
find ui/src -maxdepth 2 -type d | sort
rg -n "include_router|all_routers" api/routes/main.py api/services/integrations
rg -n "register\\(|ProviderSpec|register_package|create_runtime_sessions|run_completion" api/services/telephony api/services/integrations
```
