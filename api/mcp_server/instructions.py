"""MCP interface policy composed with Dograh's shared authoring instructions.

FastMCP sends these instructions at initialization; the host decides how to use
them. They are guidance, not enforcement. Shared stage guidance and syntax are
retrieved on demand; prompt craft lives in voice_prompting_guide atoms.

Tool names, parameters, and error codes remain authoritative in tools/list.
test_mcp_instructions_drift.py checks references against the live tool surface.
"""

from api.services.workflow.authoring import render_authoring_instructions

_MCP_INTERFACE_INSTRUCTIONS = """\
## MCP interface policy

Work interactively through the host's conversation. During planning, present a
business brief and wait for user confirmation before writing code. For an edit,
confirm the intended change; reuse context and decisions already settled.

### Reading product documentation

Prompt-authoring craft comes from the shared voice guide. For product mechanics
(how nodes work at runtime or how variables resolve), use `search_docs` first,
then `read_doc` to read the relevant full page. Use `list_docs` to browse: section
paths feed back into `list_docs` and page paths into `read_doc`.

### Creating a reusable tool

If authentication is needed, use `list_credentials` to find an existing
credential; the user creates secrets in the UI. Use `create_tool` with a typed
definition following its request schema. Use the returned identifier in the
workflow's corresponding node property.

### Loading and submitting workflows

- For edits, locate the workflow with `list_workflows` and load its current source
  using `get_workflow_code` before applying the shared authoring procedure.
  Submit with `save_workflow`, which writes a draft and leaves the published
  definition untouched. A display-name change is applied immediately.
- For a new workflow, follow the shared procedure and submit with `create_workflow`.
  This creates version 1 **published** and returns the new workflow identifier.
  Make that effect clear when confirming the plan. Subsequent edits use
  `save_workflow`. A single `startCall` node is sufficient if the user asks only
  for a starter; do not add unnecessary stages.
- Review with the shared guide before submitting. After submission, report what
  the tool actually saved or created and any outstanding configuration needs.

### Iterating on errors

A failed `save_workflow` / `create_workflow` returns `saved` / `created` false,
an `error_code`, and an `error` message, with `line` and `column` when available.
The tools' descriptions document the codes. Correct the reported problem and
resubmit the complete source; these tools do not accept patches. Retry an
internal or transient failure once before explaining it to the user.
"""

DOGRAH_MCP_INSTRUCTIONS = render_authoring_instructions(_MCP_INTERFACE_INSTRUCTIONS)
