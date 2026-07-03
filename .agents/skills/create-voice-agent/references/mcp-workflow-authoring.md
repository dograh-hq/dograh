# MCP Workflow Authoring

Use Dograh MCP as the source of truth. Do not rely only on local memory.

## Local Source Files

- MCP server docs: `docs/integrations/mcp.mdx`
- MCP tool docs: `docs/voice-agent/tools/mcp-tool.mdx`
- Workflow schema: `docs/developer/workflow-schema.mdx`
- MCP tools implementation:
  - `api/mcp_server/tools/create_workflow.py`
  - `api/mcp_server/tools/save_workflow.py`
  - `api/mcp_server/tools/get_workflow_code.py`
  - `api/mcp_server/tools/catalog.py`
  - `api/mcp_server/tools/node_types.py`
  - `api/mcp_server/tools/voice_prompting_guide.py`
- Typed SDK factories:
  - `sdk/typescript/src/typed/start-call.ts`
  - `sdk/typescript/src/typed/agent-node.ts`
  - `sdk/typescript/src/typed/global-node.ts`
  - `sdk/typescript/src/typed/end-call.ts`

## MCP Tools to Use

- `list_workflows(status="active")`: inspect current agents and avoid name collisions.
- `get_workflow(workflow_id)` / `get_workflow_code(workflow_id)`: inspect existing agents if editing or comparing.
- `list_node_types()` then `get_node_type(name)`: discover schema before using node fields.
- `list_tools(status="active")`: get existing `tool_uuid`, name, category, and description. Attach only matching existing tools.
- `list_documents()`: get `document_uuid` values if attaching knowledge-base docs.
- `list_credentials()`: get credentials only when needed for pre-call fetch or webhook nodes.
- `get_voice_prompting_guide(stage=...)`: fetch Dograh's authoritative prompt guidance.
- `create_workflow(code)`: create and publish a new workflow as version 1.
- `save_workflow(workflow_id, code)`: only for editing an existing workflow; it saves a draft.

## Creation Behavior

`create_workflow(code)` accepts full TypeScript source using `@dograh/sdk`. It parses the AST without executing code, validates node specs and graph rules, and creates a new published workflow v1.

On failure it returns `created: false`, `error_code`, and `error`. Resubmit the full corrected source. Do not send patches.

Common failure codes:

- `parse_error`: disallowed TypeScript shape or malformed code.
- `validation_error`: unknown field, missing required field, invalid enum, wrong type.
- `schema_validation`: DTO rejection.
- `graph_validation`: graph structure problem, missing start node, unreachable node, bad edge.
- `missing_name`: `new Workflow({ name: "..." })` is absent or empty.
- `trigger_path_conflict`: trigger path already exists.
- `bridge_error`: internal/transient; retry once, then surface.

## SDK Code Shape

Use simple top-level statements only:

```ts
import { Workflow } from "@dograh/sdk";
import { startCall, globalNode, agentNode, endCall } from "@dograh/sdk/typed";

const wf = new Workflow({ name: "property_management_reception" });

const global = wf.addTyped(globalNode({
  name: "Global",
  prompt: `...`
}));

const start = wf.addTyped(startCall({
  name: "Greeting",
  greeting_type: "text",
  greeting: "Good morning, thank you for calling Acme Lettings. How can I help you today?",
  prompt: `...`,
  add_global_prompt: true,
  allow_interrupt: true
}));

const close = wf.addTyped(endCall({
  name: "Successful Close",
  prompt: `...`,
  add_global_prompt: true
}));

wf.edge(start, close, {
  label: "Issue resolved",
  condition: "The caller's issue has been handled and they do not need anything else",
  transition_speech: "Okay, I've got that logged."
});
```

The validator unwraps `await`, but prefer no top-level await. Imports are allowed. Use `const` bindings. Do not use helper functions, loops, object spreads, computed values, or arbitrary top-level statements.
For MCP `create_workflow`, use snake_case edge option fields (`transition_speech`, `transition_speech_type`, `transition_speech_recording_id`) because the AST parser validates those exact wire-format names.

## Node Notes

- Exactly one `startCall` or `trigger` node is required. For voice calls, use `startCall`.
- Use at most one `globalNode`.
- Set `add_global_prompt: true` on prompted nodes that should include the global prompt.
- Attach existing tools with `tool_uuids: ["..."]` only when the exact tool exists from `list_tools`.
- For missing tools, do not fabricate UUIDs. Put tool-call instructions in the prompt and list manual tools after creation.
- Use edges for transitions. Edge `condition` should be the source of truth for when to move nodes.
- Use end nodes for terminal outcomes. Do not rely only on prompt text to end calls.
