# Client Input Intake

Use this when converting messy client material into a Dograh agent plan.

## Supported Input Formats

- Call script.
- Real call transcript.
- Series of messages the agent should say.
- SOP or help-center notes.
- Sales/qualification brief.
- Mixed client chat thread.
- Partial requirements plus examples.

## Normalize Into This Brief

- `agent_name`: proposed workflow/agent name.
- `company_name`: exact brand or placeholder approved by user.
- `call_direction`: inbound or outbound.
- `caller_type`: tenant, customer, lead, patient, applicant, etc.
- `primary_goal`: what success means.
- `secondary_goals`: optional.
- `language_region`: e.g. UK English, US English, Hinglish.
- `tone`: calm, warm, direct, high-energy, formal, etc.
- `opening_line`: exact if supplied.
- `must_say`: mandatory lines.
- `must_not_say`: forbidden claims.
- `verification`: identity checks and privacy boundaries.
- `intents`: routes/reasons the caller may have.
- `data_to_collect`: fields and whether readback is required.
- `policies`: timeframes, pricing, eligibility, refund, escalation rules.
- `human_handoff`: transfer/callback rules.
- `end_states`: success, not interested, unsupported, wrong number, transfer, callback.
- `existing_tools`: exact tools from `list_tools` that can be attached now.
- `missing_tools`: manual tools that prompts should reference.
- `documents`: knowledge-base docs to attach if available.
- `open_questions`: only blockers or high-risk unknowns.

## Clarifying Questions

Ask 3-4 questions max at a time. Good questions are specific:

- "What exact company name should the agent say?"
- "Should I preserve the script wording closely, or rewrite it into a more natural phone style?"
- "For urgent maintenance, what response timeframe should the agent promise?"
- "Can the agent share account balances, or must it escalate all balance questions to accounts?"
- "What tool names already exist for transfer, end call, or lookup actions?"
- "Should unsupported callers be transferred, offered a callback, or politely closed?"

Bad questions are broad:

- "Any other details?"
- "What should the agent do?"
- "What tone do you want?" when the script already implies tone.

## Safe Defaults

Use only when they do not invent business facts:

- Keep first version to one global node, 3-6 conversational nodes, and 1-3 end nodes.
- Use `allow_interrupt: true` for normal conversation nodes.
- Use `add_global_prompt: true` on all prompted nodes.
- Put shared objections, speech handling, and guardrails in the global prompt.
- Put extraction in `extraction_prompt` / `extraction_variables`, not in the spoken prompt.
- Prefer asking one question per turn over collecting multiple fields at once.

## Unsafe Assumptions

Do not invent:

- Legal/compliance claims.
- Prices, balances, discounts, refund eligibility.
- Appointment availability.
- Service-level timeframes.
- Transfer phone numbers.
- Customer account status.
- Qualification/disqualification rules.
- Whether AI disclosure is required.
- Whether payment can be taken by phone.

Ask or use explicit placeholders if the user approves.
