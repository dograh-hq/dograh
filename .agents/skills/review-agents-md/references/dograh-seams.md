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
- `pipecat/`, which is the upstream Pipecat git submodule; Dograh-owned
  adapters around it live under `api/services/pipecat/`

Other top-level subtrees that can matter during hierarchy reviews:

- `evals/` for STT evaluation tooling and the separate app under
  `evals/visualizer/`
- `sdk/` for packaged SDK work, especially `sdk/python/`, `sdk/typescript/`,
  and the shared generators under `sdk/codegen/`
- `examples/` for Python and TypeScript SDK examples
- `deploy/` for Helm, managed-Traefik, and shared deployment templates;
  root Compose files, `config/`, and `nginx/` hold related deployment inputs

Root `AGENTS.md` should stay at this level.

## Backend anchors

### Route aggregation

- top-level FastAPI wiring lives in `api/app.py`: it mounts the REST router
  under `/api/v1` and the Dograh MCP server under `/api/v1/mcp`
- REST routers are aggregated in `api/routes/main.py`.
- Telephony has its main cross-provider route file at `api/routes/telephony.py`.
- Integration package routers are mounted through `api.services.integrations.all_routers()`.
- Node-type metadata is exposed from `api/routes/node_types.py`.

### Route, service, and database boundary

The live backend does not enforce a mandatory route -> service -> database
call chain.

- `api/db/db_client.py` composes the specialized clients under `api/db/`; all
  runtime persistence-query construction, ORM eager-loading choices, direct
  session use, and transaction details must live in this package
- authenticated handlers commonly call `api.db.db_client` directly for simple,
  organization-scoped reads and CRUD; examples span workflows, folders, tools,
  credentials, campaigns, and telephony configuration
- reusable multi-step orchestration and cross-domain policy belong in
  `api/services/` (and background orchestration in `api/tasks/`); a route may
  combine those helpers with direct scoped client calls
- tenant isolation is an additional non-negotiable boundary: derive the
  organization from an authenticated or otherwise verified context and pass it
  into a scoped client method, or establish ownership with a scoped parent
  lookup before using a child identifier
- direct SQLAlchemy query construction or session use in another runtime
  application package is a boundary violation, not an accepted exception


### Workflow execution

Workflow execution is not a single folder.

- workflow graph, DTOs, node data, node-spec generation, text-chat execution,
  QA, and tool helpers live under `api/services/workflow/`
- live pipeline execution lives under `api/services/pipecat/`
- Dograh-specific realtime provider adapters live under `api/services/pipecat/realtime/`
- post-call QA, registered integrations, and webhook execution live in `api/tasks/run_integrations.py`

If `api/AGENTS.md` implies workflow execution lives in only one place, treat that as suspicious.

### Node spec and SDK seam

- core node specs are registered lazily from `api/services/workflow/dto.py` by `api/services/workflow/node_specs/__init__.py`
- integration node specs are merged through `api.services.integrations.all_node_specs()`
- the node catalog is exposed to REST clients from `api/routes/node_types.py`
  and to MCP clients from `api/mcp_server/tools/node_types.py`
- `scripts/generate_sdk.sh` reads the in-process node-spec registry for typed
  Python/TypeScript node APIs and a filtered OpenAPI surface for generated SDK
  models and client methods
- committed language-SDK output lives under `sdk/python/src/dograh_sdk/` and
  `sdk/typescript/src/`; the UI client under `ui/src/client/` is generated
  separately with the `ui` package's `generate-client` script

### Dograh MCP server

Do not confuse the Dograh-hosted MCP API with customer-configured MCP tools used
inside a workflow.

- `api/app.py` mounts the stateless FastMCP application created in
  `api/mcp_server/server.py`
- MCP authentication and tool implementations live under `api/mcp_server/`,
  with tool registration centralized in `api/mcp_server/server.py`
- workflow authoring tools use `api/mcp_server/ts_bridge.py` and the AST-only
  TypeScript bridge under `api/mcp_server/ts_validator/`, then validate through
  the normal workflow DTO and graph layers
- customer-configured MCP tool sessions instead live in
  `api/services/workflow/mcp_tool_session.py` and
  `api/services/workflow/tools/mcp_tool.py`

### Telephony

Current telephony architecture is registry-driven.

- importing `api.services.telephony` eagerly loads `api/services/telephony/providers/` so provider packages self-register
- provider registration and `ProviderSpec` live in `api/services/telephony/registry.py`
- provider lookup, org-scoped config normalization, inbound matching, and run-scoped resolution live in `api/services/telephony/factory.py`
- per-provider HTTP routers live in `api/services/telephony/providers/<name>/routes.py` and are auto-mounted by `api/routes/telephony.py`
- provider-local implementations live in `api/services/telephony/providers/<name>/`
- organization-scoped telephony configuration CRUD, provider form metadata,
  and phone-number management live in `api/routes/organization.py`, backed by
  `api/db/telephony_configuration_client.py` and
  `api/schemas/telephony_config.py` rather than provider route modules
- current provider packages include `ari`, `cloudonix`, `plivo`, `telnyx`, `twilio`, `vobiz`, and `vonage`
- not every provider has an HTTP route module; for example, `ari` is transport-focused and skipped by the auto-mounter
- ARI-specific external-PBX adapters have their own registry under
  `api/services/telephony/providers/ari/external_pbx/`

### Integrations

Current integrations are also registry-driven.

- package discovery lives in `api/services/integrations/loader.py` via `pkgutil.iter_modules(...)`
- package registration and runtime/completion orchestration live in `api/services/integrations/registry.py`
- shared package/session context types live in `api/services/integrations/base.py`
- concrete self-registering package examples live at
  `api/services/integrations/paygent/` and `api/services/integrations/tuner/`

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

- generated API client code lives under `ui/src/client/`: generated root files
  sit alongside the `client/` and `core/` generated subtrees; configure it in
  `ui/openapi-ts.config.ts` and regenerate it via the `ui` package script
- auth exports live in `ui/src/lib/auth/index.ts`
- auth provider wrappers live under `ui/src/lib/auth/providers/`
- server-side auth helpers live in `ui/src/lib/auth/server.ts`
- `AuthProvider` chooses between the Stack and local wrappers after fetching `/api/config/auth`, so docs that treat auth as compile-time static are suspicious

## Known drift example from the audit

`api/services/telephony/README.md` is still stale in the current repo snapshot:

- it described flat provider files like `twilio_provider.py` and `vonage_provider.py`
- it told contributors to add schemas to `api/schemas/telephony_config.py`
- it referenced legacy patterns such as direct `TwilioService` usage
- it described the old organization-configuration storage shape and claimed
  compatibility through `/api/v1/twilio/*` redirects and a retained
  `TwilioService`; the live runtime uses dedicated telephony-configuration
  tables and provider-package routes instead

The live code instead uses provider packages under `providers/<name>/`, registry-driven provider resolution, and route auto-mounting from `api/routes/telephony.py`. Use this as a reminder that prose in adjacent docs may have drifted even when the code is coherent.

## Hotspot heuristics

These are review prompts, not frozen conclusions.

- pay extra attention to deep subtrees that define extension contracts, registration points, or multi-file execution paths
- in Dograh, common examples include telephony, workflow execution, the Dograh
  MCP server, generated SDK surfaces, deployment charts, and other service
  subtrees that span many files

Ask:

- does the parent doc have enough room to explain this subtree accurately without becoming overloaded?
- does the subtree have distinct extension rules, registration points, or local pitfalls?
- would a contributor benefit from a dedicated `AGENTS.md` here?
