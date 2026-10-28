## Explicit integration changes

Only when the approved iteration requests a tool, knowledge source, recording,
or integration change, inspect the relevant schema with `get_node_type` and
the necessary catalog: `list_tools`, `list_documents`, `list_credentials`, or
`list_recordings`. Do not enumerate unrelated resources or node types. Catalog
metadata is not document content. Use only returned identifiers and capabilities.
Preserve existing bindings when the requested change does not affect them.

If creating a tool is requested, follow the interface's tool-creation permissions.
If unavailable, state the configuration gap; never fabricate a tool or credential.
