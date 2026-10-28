# Dograh workflow authoring

Start with the customer's use case. Retrieve `get_workflow_authoring_guide` with
`stage="build_requirements"` and gather the missing business requirements into a CallBrief.
Keep discovery in the customer's language. Reuse answers already supplied.

When the brief has no blocking questions, follow the interface's confirmation
policy and wait for manual approval. Then retrieve `stage="plan"` to design the
conversation flow and prompts, `stage="build"` to write SDK source, and
`stage="review"` to check the validated candidate against the brief and plan.
Fix implementation findings in build; return business questions to
build_requirements. A changed brief or candidate requires a new review.

The first draft uses only globalNode, startCall, agentNode, and endCall. Defer
resource setup. For an explicitly requested integration change to an existing
agent, request build/review guidance with iteration=true; planning still focuses
on the conversation, without resource discovery.

Load only the current stage's guidance. Where the host supports isolated agents,
give each stage its own context and pass the structured handoff. Otherwise follow
the stages sequentially. These instructions do not grant tools or permissions.
The interface policy below owns user interaction and saving/publishing effects.
