"""Top-level orchestration guide surfaced to every MCP session.

Sent to the client via `FastMCP(instructions=...)` — the client bakes
this into its system prompt, so every LLM session sees it before the
first tool call. Prefer procedural orchestration here (call order, error
handling, hard constraints). Design-level per-field guidance belongs in
each `PropertySpec.llm_hint`; it flows out through `get_node_type` and
doesn't need to be repeated here.

Extend based on real LLM failures — every bullet below ideally maps to a
mistake the system has seen at least once.
"""

DOGRAH_MCP_INSTRUCTIONS = """\
You build and edit Dograh voice-AI workflows by emitting TypeScript that
uses the `@dograh/sdk` package. Workflows are stored as JSON; this server
projects them to TypeScript for editing and parses them back on save.

## Call order

1. `list_workflows` — locate the target workflow.
2. `get_workflow_code(workflow_id)` — fetch the current source (draft if
   one exists, otherwise published).
3. (optional) `list_node_types` / `get_node_type(name)` — consult before
   adding or editing a node type whose fields aren't already visible in
   the current code.
4. Mutate the code in place. Preserve existing nodes, edges, and variable
   names unless the task requires removing or renaming them.
5. `save_workflow(workflow_id, code)` — persist as a new draft. The
   published version is untouched.

## Allowed source shape

The parser is AST-only and rejects anything outside this grammar. At the
top level, only three statement forms are accepted:

    import ... from "...";                      // any import
    const <var> = <initializer>;                // bindings (see below)
    wf.edge(<src>, <tgt>, { label, condition }); // bare edge calls

`<initializer>` is one of:
    new Workflow({ name: "..." })
    wf.addTyped(<factory>({ ...fields }) [, { position: [x, y] }])
    wf.add({ type: "<nodeType>", ...fields [, position: [x, y]] })

No functions, arrow fns, loops, conditionals, ternaries, spreads,
destructuring, template interpolation, `export`, or `.map`/`.forEach`.
Data-position values must be plain literals (strings, numbers, booleans,
null, arrays/objects of same). A single `new Workflow(...)` per file —
the `name` you pass there is the workflow's display name and is applied
on save (renames propagate immediately; definition changes go to draft).

## Adding edges — explicit syntax

    wf.edge(source, target, { label: "...", condition: "..." });

Rules:
- `source` and `target` are the **bare variable identifiers** bound by
  `wf.addTyped(...)` / `wf.add(...)` — not strings, not `.id`, not inline
  factories. Both must be declared earlier in the file.
- `label` is a short tag (≤4 words) shown in call logs to identify the
  branch: `"qualified"`, `"wrap up"`, `"retry"`.
- `condition` is a full natural-language predicate the runtime evaluates
  against the live conversation: `"caller confirmed interest in a demo"`,
  not `"interested"`. Condition clarity determines routing accuracy.
- Both fields are required and must be non-empty strings.
- Edges are directional; emit one `wf.edge(...)` per outgoing branch.
- Place all edges after all node bindings; group by source node.

Example:

    const greet = wf.addTyped(startCall({ name: "Greet", prompt: "Hi!" }));
    const done  = wf.addTyped(endCall({ name: "Done", prompt: "Bye." }));
    wf.edge(greet, done, {
        label: "wrap up",
        condition: "user acknowledged the greeting and is ready to end"
    });

## Hard graph constraints

- Exactly one `startCall` node per workflow; no incoming edges.
- `endCall` nodes have no outgoing edges.
- `globalNode` has no incoming or outgoing edges; its prompt is prepended
  to every other node's prompt at runtime when that node sets
  `add_global_prompt=true`.
- Every non-global node must be reachable from `startCall`.

## Iterating on errors

`save_workflow` returns one of:
  - `parse_error`      Disallowed construct (see grammar above) or
                       malformed TypeScript.
  - `validation_error` Node data failed spec validation (unknown field,
                       missing required, wrong type, bad `options` value).
  - `graph_validation` Structural rule broken (missing startCall,
                       unreachable node, edge to/from wrong node type).
  - `bridge_error`     Internal — retry once, then surface to the user.

Every error carries `line` and `column`. Fix at that location and
resubmit the **complete source** — this tool does not accept patches.

## Field conventions

- `data.name` is the canonical identifier. Pick a descriptive name
  (`"Qualify Budget"`, not `"Node1"`) — the generated code uses it as
  the variable name and call logs reference it.
- Reference fields take UUIDs, not human names:
    `tool_refs`, `document_refs` → from `list_tools`, `list_documents`
    `credential_ref`             → from `list_credentials`
    `recording_ref`              → from `list_recordings`
- `mention_textarea` fields (prompts, greetings, etc.) accept
  `{{template_variables}}` — values resolved at runtime from
  `pre_call_fetch`, caller context, or earlier extraction passes.

## Style

- Prefer `wf.addTyped(factory({ ... }))` over `wf.add({ type, ... })`.
- Only include fields whose values differ from the spec default — the
  parser re-applies defaults on save, so extras are noise.
- Omit `position`; the server reconciles positions against the previous
  saved workflow and lays out new nodes automatically.
- Add nodes in call-flow order (start → intermediate → end) so the
  generated code reads top-to-bottom, with all edges after all nodes.
"""
