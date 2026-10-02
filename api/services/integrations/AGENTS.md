# Integrations - Plugin Contract

`api/services/integrations/` is the extension seam for third-party integrations.
New integrations should be self-contained here. Do not bleed integration-specific
logic into `workflow/dto.py`, `workflow/node_specs/`, `run_pipeline.py`,
`event_handlers.py`, or `run_integrations.py` unless you are changing the generic
framework itself.

## Golden Path

Create a package:

```text
api/services/integrations/<name>/
├── __init__.py
├── node.py
├── runtime.py        # optional
├── completion.py     # optional
├── tools.py          # optional
├── routes.py         # optional
└── client.py         # optional
```

The package self-registers on import via `register_package(...)`. Discovery is
automatic: `api/services/integrations/loader.py` imports every submodule under
`api.services.integrations` except the reserved internal names `base`, `loader`,
and `registry`.

## Registration Pattern

`__init__.py` should register one `IntegrationPackageSpec`, following the
existing integration packages in this directory.

Use:

```python
PACKAGE = register_package(
    IntegrationPackageSpec(
        name="<package_name>",
        nodes=(NODE,),
        create_runtime_sessions=create_runtime_sessions,  # optional
        run_completion=run_completion,  # optional
        create_tools=create_tools,  # optional
        routers=(router,),  # optional
    )
)
```

The package name is the registry key. The node `type_name` is the workflow node
type string and must stay stable once exposed.

## Node Model + Spec

For integration nodes, the Pydantic model is the source of truth. The serialized
`NodeSpec` is derived from it.

Refer to an existing integration node for the overall structure:

- Define one Pydantic model per node, inheriting
  `api/services/workflow/node_data.py:BaseNodeData`.
- Annotate it with `@node_spec(...)`.
- Define fields with `spec_field(...)`.
- Generate the external spec with `SPEC = build_spec(ModelClass)`.
- Register the node with `IntegrationNodeRegistration(...)`.

Important rules:

- Put runtime validation in the model, not in the generated spec.
  Example: conditional requiredness belongs in `@model_validator(mode="after")`.
- Keep `@node_spec(name=...)` and `IntegrationNodeRegistration.type_name`
  identical. They are the same workflow node type string.
- Put wire constraints in the field itself where possible.
  Example: `gt=0`, `min_length=1`, `pattern=...`.
- Put UI/export-only differences in `field_overrides`.
  Use this for `display_name`, `description`, `required`, `spec_default`,
  `display_options`, or property ordering.
- Use `spec_exclude=True` for internal fields that must exist in persisted data
  but must not show up in `/api/v1/node-types`.
- Set `property_order=(...)` in `@node_spec(...)` when the editor field order
  must remain stable.

Typical workflow graph constraints for configuration-only integration nodes:

```python
GraphConstraints(min_incoming=0, max_incoming=0, min_outgoing=0, max_outgoing=0)
```

These constraints control how the node can be connected in the workflow graph.
Use them for configuration nodes that are not conversational graph steps.

## Secret Fields

If the node stores secrets, register them in
`IntegrationNodeRegistration.sensitive_fields`.

That is enough for generic masking / masked round-trip preservation via
`api/services/configuration/masking.py`. Do not add new integration-specific
masking branches unless you are changing the shared masking framework.

## No Central DTO Edits

Do not add integration node classes to `api/services/workflow/dto.py`.

Integration nodes are resolved dynamically through:

- `get_node_data_model()` in `workflow/dto.py`
- `get_node_spec()` / `all_node_specs()` in `services/integrations/registry.py`

`RFNodeDTO` validates integration nodes by `type` through the registry. That is
the intended extension path.

## Live Call Path

If the integration needs live call data, implement `create_runtime_sessions(...)`
in `runtime.py` and return `IntegrationRuntimeSession` objects.

The generic wiring is already in `api/services/pipecat/run_pipeline.py`:

- `create_runtime_sessions(IntegrationRuntimeContext(...))` is called before the
  pipeline task starts.
- Each returned session gets `session.attach(task)` called.

Use this only for lightweight live collection:

- attach task observers
- read context messages
- capture timing / turn / tool events
- build an in-memory snapshot

Do not do outbound network I/O in the live path unless there is a very strong
reason. Prefer the standard pattern: collect live, deliver after the call.

`IntegrationRuntimeContext` gives you:

- `workflow_run_id`
- `workflow_run`
- `workflow_graph`
- `run_definition`
- `user_config`
- `is_realtime`
- `context_messages_provider`

Typical runtime pattern:

- scan `context.workflow_graph.nodes.values()` for enabled nodes of your type
- if none are enabled, return `[]`
- build one collector/session per workflow run, not per node, unless the
  integration truly needs multiple independent collectors

## LLM Tool Path

If the integration gives the agent something to call during the conversation,
implement `create_tools(workflow_graph, *, realtime)` in `tools.py` and return
`IntegrationTool` objects (name, description, JSON schema properties, required
parameters, and an async handler that receives pipecat `FunctionCallParams`).

`PipecatEngine._prepare_node` calls it through `create_integration_tools(...)`
for every Start Call and Agent node, with the graph of the agent that owns the
node, registers each handler with the LLM, and adds each schema to the node's
tools. This covers voice calls, realtime calls, and text chat. A tool whose name
the node already uses (a transition, custom tool or knowledge base tool) is
skipped with a warning, so an integration can never replace a workflow function.

Rules for tool factories:

- Return `[]` when the workflow has no enabled node of your type.
- Keep the factory cheap and synchronous. It runs on every node transition.
- Start slow setup (downloads, warm-up) in the background and cache it, so the
  first tool call does not pay for it and later calls reuse it.
- Handlers report errors through `params.result_callback(...)` rather than
  raising. A factory that raises loses its tools for that node; the call goes on.

## Turn Context Path

If the integration can answer from the caller's words without the LLM deciding
to ask, implement `create_context_providers(workflow_graph)` and return async
callables that take the latest user message and return text, or `None`.

Before the LLM answers each user turn, `IntegrationContextProcessor` (voice) and
the text chat runner call every provider of the active agent and append their
text to the node prompt for that inference. This saves the LLM round trip a
tool call costs. Realtime calls get no providers, so offer a tool there.

Rules for context providers:

- Answer in milliseconds. A provider has `PROVIDER_TIMEOUT_SECONDS` (0.25 s) and
  is skipped for the turn when it runs over or raises.
- Return `None` instead of waiting when your data is not ready yet.
- Keep the text short and relevant: it is part of the prompt of every turn.

## Call-Finish Snapshot Path

`api/services/pipecat/event_handlers.py` finalizes runtime sessions before the
engine is cleaned up.

The generic flow:

1. `on_pipeline_finished` builds `gathered_context`
2. each runtime session gets `await session.on_call_finished(...)`
3. returned dicts are merged into `integration_logs`
4. those logs are persisted into `workflow_run.logs`

Use `on_call_finished(...)` to emit a compact, serializable snapshot that the
post-call completion handler can consume later. Return `None` if there is nothing
to persist.

This is the handoff between the live call path and the post-call task path.

## Post-Call Completion Path

If the integration needs durable artifacts, public URLs, retries, or external
delivery, implement `run_completion(nodes, context)` in `completion.py`.

The generic orchestration is already in `api/tasks/run_integrations.py`:

1. load the pinned workflow definition from the workflow run
2. create a public token if post-call work exists
3. run QA nodes first
4. run registered integration completion handlers
5. run webhook nodes last

Your handler receives:

- `nodes`: raw workflow node dicts for your node types only
- `IntegrationCompletionContext`:
  - `workflow_run_id`
  - `workflow_run`
  - `workflow_definition`
  - `definition_id`
  - `organization_id`
  - `public_token`

Expected completion handler pattern:

- validate each node with `YourNodeData.model_validate(node.get("data", {}))`
- skip disabled nodes
- read any runtime snapshot from `context.workflow_run.logs`
- build durable URLs using `public_token` when appropriate
- perform external delivery
- return a result dict keyed per node, usually with `node_id` embedded

Returned data is merged into `workflow_run.annotations`.

Do not assume completion runs inside the live pipeline process. Treat it as a
separate post-call worker step.

## Optional Routes

If an integration exposes HTTP routes, put them in `routes.py` and include the
router in `IntegrationPackageSpec.routers`.

Routers are mounted automatically by `api/routes/main.py` through `all_routers()`.
Do not edit `routes/main.py` for per-integration route wiring.

## Import Discipline

Keep package import side effects light.

The integration loader runs during:

- node-type/spec enumeration
- tests
- route startup
- registry access

So avoid top-level imports that require environment variables, network access,
or heavyweight initialization when possible. Prefer lazy imports inside
`run_completion()` / `create_runtime_sessions()` if the dependency is optional or
environment-sensitive.

## Testing Expectations

At minimum, new integrations should add coverage for:

- node model validation
- generated spec/example validity
- secret masking + masked round-trip preservation if secrets exist
- runtime snapshot creation if live collectors exist
- completion handler happy path and disabled-node skip path
- tool factory output and handler results if the package adds LLM tools

If you change shared integration machinery, test the framework in the generic
code path, not only the concrete integration.
