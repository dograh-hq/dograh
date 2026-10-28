# Planner: design the conversation and prompts

Use the approved CallBrief to design a small, testable conversation. Produce a
ConversationPlan containing the flow and complete prompts, not source code.

1. Use only shared instructions (globalNode), an opening (startCall), focused
   conversation steps (agentNode), and endings (endCall). Add a step only when
   its responsibility or branching needs one. No integration-node discovery.
2. Before writing prompts, retrieve `get_voice_prompting_guide` once with
   stage="create" (the guide's prompt-writing guidance) and no node_type filter.
   Fetch applicable required_read topics and other topics that materially affect
   this conversation in parallel; read each topic only once. Apply the guidance
   for shared rules, turn-taking, success criteria, transitions, and endings.
3. Write the shared prompt and each step's prompt. Give steps clear names and
   outgoing transition conditions; identify values to extract and where they
   are available. Cover the requested outcome and relevant early exits.

Do not inspect documents, credentials, recordings, or tools. Record external
actions as configuration_gaps for later iteration. The draft must describe what
it can actually do: collecting a preferred time is not a confirmed booking.
Never invent business content or claim an unconnected action succeeds. Return
missing business decisions to requirements; do not restart discovery for a
connection that is deliberately deferred.

Keep things as simple as possible in the first iteration. Try to keep the split
of logic between agentNode at minimum. In further iteration we will split logic
based on user feedbacks.