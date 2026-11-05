"""Topic: when and how the agent should call tools."""

from api.services.voice_prompting_guide._base import VoicePromptingTopic

TOPIC = VoicePromptingTopic(
    id="tool_calls",
    title="One tool, one job; specific trigger conditions; never mix text and a call",
    content="""\
Each tool should do one thing. A tool that "schedules an appointment and sends a
confirmation email and updates the CRM" fails unpredictably — split it into
three. (This is mostly a plan-time decision about tool design.)

An Edge with label Schedule Appointment is sent to llm with tool name schedule_appointment.

Be specific about when to call each tool and when not to. Conditions matter:
"Call schedule_appointment only after the user has passed all three screening
questions and confirmed the slot", not "call schedule_appointment when the user
wants an appointment." Put the firing condition in the prompt AND in the tool's
own description field — think of the description as the usage rule. If the model
picks the wrong tool or passes bad parameters, the fix is usually in the tool
description, not the prompt.

A turn is either spoken text or a tool call, never both. If the model tries to
mix a spoken response with a tool call in the same turn, most voice stacks
behave strangely. Make this explicit in the prompt.

Handle tool errors gracefully. On an error, the agent should say something like
"I'm having an issue with our system, let me try again." If it errors a second
time, apologize and offer to have someone call them back — don't loop the
caller through three failed retries.

To avoid dead air during a slow call, have the agent say one short line before
calling a tool — "okay, give me a second" or "I'm checking that now" — then
call the tool immediately.

Summarize tool routing as condition → action rules in the node's success-criteria
section. For example: "Call schedule_appointment only after all three screening
questions pass and the caller confirms the slot." Name the required inputs and
where they come from; do not invent missing values. Wait for the tool's result
before claiming the action succeeded.

When editing an existing agent, use its actual tool names and check the tool
description, node prompt, and inherited global instructions together. Change
only the affected calling rules and preserve unrelated prompts and branches.
""",
)
