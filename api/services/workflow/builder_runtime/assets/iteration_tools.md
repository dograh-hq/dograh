## Tools

A tool is a reusable object the agent invokes during a call (an HTTP API call, a
transfer, end call, a calculator, or an MCP server's tools). A node can only use
tools listed in its `tool_uuids`.

- Reuse before creating. Check `list_tools` and read candidates with `get_tool`.
  If one already does the job, reference its `tool_uuid`; do not create a
  duplicate.
- Creating a tool: when the requested change needs one that does not exist, call
  `get_tool_definition_schema` for its category, then
  `propose_tool` with the full definition and a one-paragraph `plain_language`
  summary of what it does, when it runs, and what it sends. It is created only
  after the user approves; put the returned `tool_uuid` in the node's
  `tool_uuids`. A tool does nothing unless a node both lists it and the node's
  prompt tells the agent when to call it — add both in the same candidate.
  Use the returned identifier directly; creation already confirms it exists.
  Omit optional settings unless the request needs them. A simple phone transfer
  needs type="transfer_call" and config.destination; introductions are optional.
  For dynamic or context-based transfers, request the schema with the matching
  destination_source, preserving the existing mode when editing its definition.
- Editing a tool: read it with `get_tool`, then call `propose_tool_edit` with
  only the fields that change. A `definition` is the full replacement, not a
  patch. Read `get_tool_definition_schema` with operation="update" before changing
  a definition; reuse schemas already read. Its type cannot differ from the
  tool's category. The edit changes
  the shared tool for every workflow that uses it — say so in `plain_language`;
  if the change should not affect other workflows, create a separate tool.
- Leave an unknown endpoint URL as a clear placeholder and list it as a
  configuration gap; never guess a real URL. Authenticated HTTP and MCP tools
  reference a `credential_uuid` from `list_credentials` — never put secrets in
  the definition.

Pick the category for the requested action:
- http_api — call an external system (create a ticket, look up an order).
  LLM-supplied values go in `parameters`; values Dograh already holds go in
  `preset_parameters` as `{{gathered_context.*}}`/`{{initial_context.*}}`.
- transfer_call — hand the caller to a phone number: a static `destination`, or
  `context_mapping`/`dynamic` resolution when the number depends on the call.
- transfer_agent — hand the live call to another of your agents by its
  `workflow_id`; that agent must already exist.
- end_call — let the agent hang up; enable `endCallReason` only if a recorded
  reason is needed.
- calculator — arithmetic during the call; no configuration.
- mcp — expose a customer MCP server's tools; set `url`, an optional
  `tools_filter`, and a `credential_uuid` if it needs auth.
