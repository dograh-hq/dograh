## Explicit integration changes

Only when the requested edit involves a tool, knowledge source, recording,
or integration change, inspect the necessary catalog: `list_tools`, `list_documents`, `list_credentials`, or
`list_recordings`. Do not enumerate unrelated resources or node types. Catalog
metadata is not document content. Use only returned identifiers and capabilities.
Preserve existing bindings when the requested change does not affect them.
For startCall and agentNode, tool_uuids is an array of tool UUID strings. Add the
binding and its calling instructions together. Read `get_node_type` only for fields
or node types not covered by the supplied reference.

Check the affected tool, node prompt, and inherited global rules
together. Keep their trigger conditions consistent and preserve unrelated
prompt wording, nodes, and branches. Call a tool only when its stated trigger is
met, and do not claim success before its result. Tool-configured announcements
supply their own speech; do not duplicate that speech in the node prompt.

If creating a tool is requested, follow the interface's tool-creation permissions.
If unavailable, state the configuration gap; never fabricate a tool or credential.
