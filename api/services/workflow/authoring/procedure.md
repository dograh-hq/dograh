# Dograh workflow authoring

For a new agent, start with the customer's use case. Retrieve `get_workflow_authoring_guide` with
`stage="build_requirements"` and gather the missing business requirements into a CallBrief.
Keep discovery in the customer's language. Reuse answers already supplied.

When the brief has no blocking questions, follow the interface's confirmation
policy and wait for manual approval. Then retrieve `stage="build"` to turn the
approved CallBrief into conversation flow, prompts, and SDK source. Retrieve
`stage="review"` to check the validated candidate against the brief.
Fix implementation findings in build; return business questions to
build_requirements. A changed brief or candidate requires a new review.

The first draft uses only globalNode, startCall, agentNode, and endCall. Defer
resource setup.

For an existing agent, load its current source and retrieve stage="build" with
iteration=true. Edit directly from the requested change; ask only for missing
details that affect it. No new CallBrief or requirements approval is needed.
Review with iteration=true against the requested change and existing behavior.

Load only the current stage's guidance. Where the host supports isolated agents,
give each stage its own context and pass the structured handoff. Otherwise follow
the stages sequentially. These instructions do not grant tools or permissions.
The interface policy below owns user interaction and saving/publishing effects.
