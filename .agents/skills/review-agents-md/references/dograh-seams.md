# Dograh Seams

Use this file as a starting map, not as source of truth. Verify every claim against the live repo.

## Dynamic discovery only

Do not record the current `AGENTS.md` inventory in this file.

- discover the live hierarchy with `rg --files -g 'AGENTS.md' .`
- use the helper script to identify uncovered hot spots
- treat any baked-in inventory of existing `AGENTS.md` files as drift-prone and remove it

## Root-level anchors

The repo root still revolves around these contributor-relevant directories:

- `api/`
- `ui/`
- `scripts/`
- `docs/`
- `pipecat/`

Current code-heavy top-level subtrees that can matter during hierarchy reviews:

- `evals/` for evaluation tooling, including `evals/visualizer/`
- `sdk/` for packaged SDK work, especially `sdk/python/` and `sdk/typescript/`

Root `AGENTS.md` should stay at this level.

## Backend anchors

### Route aggregation

- REST routers are aggregated in `api/routes/main.py`.
- Telephony has its main cross-provider route file at `api/routes/telephony.py`.
- Integration package routers are mounted through `api.services.integrations.all_routers()`.
- Node-type metadata is exposed from `api/routes/node_types.py`.

### Workflow execution

Workflow execution is not a single folder.

- workflow graph, DTOs, node data, node-spec generation, QA, and tool helpers live under `api/services/workflow/`
- live pipeline execution lives under `api/services/pipecat/`
- Dograh-specific realtime provider adapters live under `api/services/pipecat/realtime/`
- post-call QA, registered integrations, and webhook execution live in `api/tasks/run_integrations.py`

If `api/AGENTS.md` implies workflow execution lives in only one place, treat that as suspicious.

### Node spec and SDK seam

- core node specs are registered lazily from `api/services/workflow/dto.py` by `api/services/workflow/node_specs/__init__.py`
- integration node specs are merged through `api.services.integrations.all_node_specs()`
- the frontend and SDK-facing node catalog is served from `api/routes/node_types.py`

### Telephony

Current telephony architecture is registry-driven.

- importing `api.services.telephony` eagerly loads `api/services/telephony/providers/` so provider packages self-register
- provider registration and `ProviderSpec` live in `api/services/telephony/registry.py`
- provider lookup, org-scoped config normalization, inbound matching, and run-scoped resolution live in `api/services/telephony/factory.py`
- per-provider HTTP routers live in `api/services/telephony/providers/<name>/routes.py` and are auto-mounted by `api/routes/telephony.py`
- provider-local implementations live in `api/services/telephony/providers/<name>/`
- current provider packages include `ari`, `cloudonix`, `plivo`, `telnyx`, `twilio`, `vobiz`, and `vonage`
- not every provider has an HTTP route module; for example, `ari` is transport-focused and skipped by the auto-mounter

### Integrations

Current integrations are also registry-driven.

- package discovery lives in `api/services/integrations/loader.py` via `pkgutil.iter_modules(...)`
- package registration and runtime/completion orchestration live in `api/services/integrations/registry.py`
- shared package/session context types live in `api/services/integrations/base.py`
- a concrete package example exists at `api/services/integrations/tuner/`

## Frontend anchors

### Navigation and pages

- page routes live under `ui/src/app/`
- `ui/src/app/layout.tsx` composes the global frontend providers and `AppLayout`
- runtime config handlers live under `ui/src/app/api/config/`
- auth/session handlers live under `ui/src/app/api/auth/`
- feature coverage should be discovered from the current `ui/src/app/` tree, not maintained as a static list here

### Components and feature slices

- shared primitives live under `ui/src/components/ui/`
- workflow builder primitives live under `ui/src/components/flow/`
- reusable workflow UI lives under `ui/src/components/workflow/`
- workflow run UI lives under `ui/src/components/workflow-runs/`
- telephony-related UI lives under `ui/src/components/telephony/`
- layout components live under `ui/src/components/layout/`
- workflow feature code is split between reusable components and route-local code under `ui/src/app/workflow/[workflowId]/`, especially `components/`, `contexts/`, `hooks/`, `stores/`, `utils/`, and nested `run/[runId]/`

### Client and auth

- generated API client code lives under `ui/src/client/`, with generated subtrees in `ui/src/client/client/` and `ui/src/client/core/`
- auth exports live in `ui/src/lib/auth/index.ts`
- auth provider wrappers live under `ui/src/lib/auth/providers/`
- server-side auth helpers live in `ui/src/lib/auth/server.ts`
- `AuthProvider` chooses between the Stack and local wrappers after fetching `/api/config/auth`, so docs that treat auth as compile-time static are suspicious

## Known drift example from the audit

`api/services/telephony/README.md` is still stale in the current repo snapshot:

- it described flat provider files like `twilio_provider.py` and `vonage_provider.py`
- it told contributors to add schemas to `api/schemas/telephony_config.py`
- it referenced legacy patterns such as direct `TwilioService` usage

The live code instead uses provider packages under `providers/<name>/`, registry-driven provider resolution, and route auto-mounting from `api/routes/telephony.py`. Use this as a reminder that prose in adjacent docs may have drifted even when the code is coherent.

## Hotspot heuristics

These are review prompts, not frozen conclusions.

- pay extra attention to deep subtrees that define extension contracts, registration points, or multi-file execution paths
- in Dograh, common examples include telephony, workflow execution, generated SDK surfaces, and other service subtrees that span many files

Ask:

- does the parent doc have enough room to explain this subtree accurately without becoming overloaded?
- does the subtree have distinct extension rules, registration points, or local pitfalls?
- would a contributor benefit from a dedicated `AGENTS.md` here?
