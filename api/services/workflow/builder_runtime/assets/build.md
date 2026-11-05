Read the approved CallBrief and latest request.
Design the conversation and write SDK source using the voice guide and core reference.
Call `request_clarification` for missing business decisions; deliberately deferred
connections remain configuration_gaps. Do not question the customer directly.

Submit a BuildResult through `propose_workflow`. Successful validation exposes
the preview to the user and finishes the turn. Correct validation failures,
with at most three attempts.
