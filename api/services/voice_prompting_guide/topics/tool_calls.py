"""Topic: when and how the agent should call tools."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="tool_calls",
    title="One tool, one job; specific trigger conditions; never mix text and a call",
    severity="high",
    applies_to_node_types=("agentNode",),
    stages={
        Stage.plan: StageLens(
            relevant=True,
            lens=(
                "Keep each tool scoped to one job — split a 'schedule + email + CRM' "
                "tool into three. Note the precise condition under which each tool "
                "should fire; that becomes the trigger wording in the prompt."
            ),
        ),
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "State the exact condition for each tool call in the prompt ('call "
                "schedule_appointment only after all three screening questions "
                "pass'). Also tell the agent a turn is either speech OR a tool call, "
                "never both, and how to recover when a tool errors."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Check each tool has a specific firing condition (not 'when the user "
                "wants it'), that the prompt forbids mixing speech with a tool call, "
                "and that tool errors have a recovery path."
            ),
        ),
    },
    content="""\
Each tool should do one thing. A tool that "schedules an appointment and sends a
confirmation email and updates the CRM" fails unpredictably — split it into
three. (This is mostly a plan-time decision about tool design.)

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

The decision tree for which tool fires when belongs in the success-criteria
section — see that topic.
""",
    audit_checks=(
        AuditCheck(
            id="specific_tool_conditions",
            judge_question=(
                "For each tool the node can call, does the prompt give a specific "
                "condition that must hold before it fires, rather than a vague "
                "trigger like 'when the user wants it'?"
            ),
            expected="yes",
            quote=(
                "Tool trigger is vague — state the exact precondition (e.g. 'only "
                "after all screening questions pass')."
            ),
        ),
        AuditCheck(
            id="forbids_text_and_tool_in_one_turn",
            judge_question=(
                "Does the prompt make clear that a turn is either spoken text or a "
                "tool call, never both in the same turn?"
            ),
            expected="yes",
            quote=(
                "Prompt doesn't forbid mixing speech and a tool call in one turn — "
                "most voice stacks misbehave when it does."
            ),
        ),
    ),
    cross_refs=("success_criteria", "end_call_logic"),
)
