---
name: create-voice-agent
description: Create production-quality Dograh voice agents from client scripts, call transcripts, message sequences, rough briefs, or mixed discovery notes. Use when the user asks to build, create, design, or implement a Dograh voice agent/workflow in a Dograh account via MCP, especially when prompts must follow Dograh's voice-agent prompting guide, ask clarifying questions, create global/agent/end nodes, connect nodes, attach existing tools where available, and leave manual tool placeholders where tools are not created yet.
---

# Create Dograh Voice Agent

Create an immediately usable Dograh voice agent in the user's Dograh account using Dograh MCP. V1 creates the workflow, global prompt, conversational nodes, end nodes, edges, and existing tool attachments where available. V1 does **not** create missing business tools; represent them in node prompts with explicit natural-language tool-call instructions.

## Read First

- For MCP mechanics, read [references/mcp-workflow-authoring.md](references/mcp-workflow-authoring.md).
- For prompt craft, read [references/prompting-guide.md](references/prompting-guide.md).
- For intake and clarifying questions, read [references/client-input-intake.md](references/client-input-intake.md).

Also call Dograh MCP `get_voice_prompting_guide(stage="plan")` before planning, `get_voice_prompting_guide(stage="create", node_type="agent")` before writing node prompts, and `get_voice_prompting_guide(stage="review")` before creating the workflow.

## Workflow

### 1. Orient to the Target Account

Use MCP to confirm the connected Dograh account/workspace:

- `list_workflows()` to understand existing agents and avoid name collisions.
- `list_tools()` to discover existing tools; only attach tools that already exist and clearly match the client use case.
- `list_documents()` if the client mentions knowledge-base docs.
- `list_credentials()` only if the workflow needs pre-call fetch or webhook/auth references.
- `list_node_types()` and `get_node_type()` for every node type used.

If no Dograh MCP server is connected, stop and tell the user to connect it. Do not create a local-only artifact as a substitute for the requested account creation.

### 2. Normalize the Client Material

Client input may be a polished script, call transcript, bullet list, loose messages, sales deck, support SOP, or pasted notes. Convert it into a compact internal brief:

- Business and agent identity.
- Inbound or outbound call.
- Call goal and success outcome.
- User types and likely intents.
- Required verification steps.
- Known policies, timeframes, prices, compliance lines, and escalation boundaries.
- Data to collect, verify, or extract.
- Existing tools/actions and tools still missing.
- Required language, region, tone, and pronunciation notes.

Never invent missing business facts. Preserve client-provided exact phrasing where useful, but ask whether to preserve script wording or rewrite into a more natural production voice style when this is unclear.

### 3. Ask Clarifying Questions Before Building

Ask only highly specific questions whose answers materially affect correctness. Prefer 3-4 questions at a time. Do not ask for cosmetic preferences if safe defaults work.

Always clarify hard blockers before creation:

- Company/brand name or whether to use a placeholder.
- Agent name/persona if absent.
- Inbound vs outbound if ambiguous.
- Main call goal and terminal success state.
- Escalation/transfer/callback policy and timeframes.
- Claims the agent is allowed or forbidden to make.
- Which actions have existing tools versus manual follow-up.

If important details remain unknown, create the agent with explicit placeholders only when the user approves placeholders for those exact facts.

### 4. Plan the Node Outline

Keep the first production version focused. Prefer 3-6 conversational nodes, plus one global node and 1-3 end nodes. Use more only when the call has clearly distinct intents.

Show a short outline before creation when the user is still shaping the agent. Once the user asks to create/build it, proceed without step-by-step consent.

Good node boundaries:

- Start/greeting and first intent capture.
- Verification or qualification.
- Intent-specific handling nodes.
- Escalation/callback/transfer handling.
- Successful close, disqualified/unsupported close, or transfer close.

Avoid one giant node that handles the whole call. Avoid a node per sentence.

### 5. Write Prompts

Use one global node for shared behavior: persona, language, phone-call constraints, transcript-noise handling, response style, guardrails, common objections, anti-jailbreak, small talk, tool-call rules, and no-fabrication rules.

Use agent nodes for local tasks: what to ask, what to verify, what to do next, when to wait, when to transition, when to call an existing or manual tool.

Rules for tool placeholders in prompts:

- Use the actual or intended tool name in snake_case.
- Put the tool name in single quotes.
- Keep `tool` outside the quoted name.
- State the trigger condition clearly.
- Example: `If the user has confirmed their order ID, then call 'get_order_details' tool.`
- If the tool does not exist yet, also list it in the final manual action items.

For node transitions, prefer real workflow edges with clear `label` and `condition`. If the prompt mentions transition behavior, align it with the edge condition exactly to avoid instruction collision.

### 6. Create the Workflow via MCP

Author full SDK TypeScript using `@dograh/sdk` and `@dograh/sdk/typed`, then call `create_workflow(code)`. A successful new workflow is published as version 1.

If `create_workflow` returns `created: false`, fix the full source and call `create_workflow` again. For partial platform or network failures, inspect what exists with `list_workflows()` / `get_workflow()` and explain the state before deciding whether to retry or repair.

### 7. Review and Report

Before finalizing, check the generated code and prompts for:

- No invented facts.
- No instruction collisions.
- Every node has a focused task and success criteria.
- Every turn asks a question or gives a clear user-response nudge unless making a tool call or ending the call.
- Critical IDs, emails, dates, payment amounts, phone numbers, and booking slots require readback.
- Text and tool calls are not mixed in the same LLM turn.
- Every manual tool placeholder appears in the manual action list.

Final response should include the created workflow ID/name, what was created, existing tools attached, manual tools still to create, and unresolved assumptions/placeholders.
