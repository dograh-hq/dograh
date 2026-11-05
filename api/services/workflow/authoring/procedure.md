# Dograh workflow authoring

For a new agent, start with the customer's use case. Retrieve `get_workflow_authoring_guide` with
`stage="build_requirements"` and gather the missing business requirements into a CallBrief.
Keep discovery in the customer's language. Reuse answers already supplied.

When the brief has no blocking questions, follow the interface's confirmation
policy and wait for manual approval. Then retrieve `stage="build"` to turn the
approved CallBrief into conversation flow, prompts, and SDK source. Validate and
fix source errors using the host's tools. `stage="review"` is optional guidance
for an explicit review, not a required stage of creation or editing.
If approved requirements change, update the source and validate it against the
new requirements before presenting the agent as ready.

The first draft uses only globalNode, startCall, agentNode, and endCall. Defer
resource setup.

For an existing agent, load its current source and retrieve stage="build" with
iteration=true. Edit directly from the requested change; ask only for missing
details that affect it. No new CallBrief or requirements approval is needed.
Load only the guidance needed for the request. These instructions do not grant tools or permissions.
The interface policy below owns user interaction and saving/publishing effects.
