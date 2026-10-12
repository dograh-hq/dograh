# Dograh workflow authoring

Start with the customer's use case. Retrieve `get_workflow_authoring_guide` with
`stage="plan"` and gather the missing business requirements into a CallBrief.
Keep discovery in the customer's language. Reuse answers already supplied.

When the brief has no blocking questions, follow the interface's confirmation
policy, then retrieve `stage="build"` to translate it into a Dograh workflow.
Retrieve `stage="review"` to check the candidate against the brief before
submission. Fix implementation findings in build; return business questions to
plan. A changed brief or candidate requires a new review.

Load only the current stage's guidance. Where the host supports isolated agents,
give each stage its own context and pass the structured handoff. Otherwise follow
the stages sequentially. These instructions do not grant tools or permissions.
The interface policy below owns user interaction and saving/publishing effects.
