# Builder: turn the approved CallBrief into a working voice agent

Use the approved CallBrief to design the conversation
and write complete SDK source. The brief owns business requirements. Use the
compact SDK reference below for syntax and field semantics.

1. Before writing prompts, use the voice guide already supplied in context.
   If it is absent, call `get_voice_prompting_guide` without arguments.
   Reuse guidance already read.
2. Keep the first draft small: use shared instructions (globalNode), an opening
   (startCall), focused conversation steps (agentNode), and endings (endCall).
   Add a step only when its responsibility or branching needs one. Group related
   information when that keeps the conversation natural.
   Reuse an ending when the same goodbye behavior is sufficient; different call
   outcomes alone do not require separate ending nodes.
3. Write the shared prompt and each step's prompt. Give steps clear names and
   explicit outgoing transition conditions, including the matching tool names.
   Identify values to extract and where they are available. Cover the requested
   outcome and relevant early exits. Let the parser supply defaults, identifiers,
   and layout.

For the first draft, defer integrations and resource bindings. Record requested
external actions as configuration_gaps without claiming they are implemented:
collecting a preferred time is not a confirmed booking. Never invent business
content or claim an unconnected action succeeds. Ask only for missing business
decisions; deliberately deferred connections do not require rediscovery.

Follow the host's submission contract. MCP clients return a BuildResult with
complete code, a short summary, and configuration_gaps. The in-app builder writes
the working copy, receives automatic validation, and finishes with a normal reply.
Correct reported source errors. Validation does not establish that a live call or
external integration works.
