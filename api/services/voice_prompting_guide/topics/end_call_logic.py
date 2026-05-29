"""Topic: consolidate end-call scenarios with clear trigger conditions."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="end_call_logic",
    title="Consolidate end-call scenarios; give each a clear trigger",
    severity="medium",
    applies_to_node_types=("endCall", "agentNode"),
    stages={
        Stage.plan: StageLens(
            relevant=True,
            lens=(
                "Enumerate the ways a call can end (success, voicemail, wrong "
                "number, disqualified, reschedule, transfer) and consolidate them "
                "into two or three end-call nodes rather than ten."
            ),
        ),
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "Give each end-call node a clear trigger condition in the prompt "
                "('call end_call_rescheduled only if the user asked for a different "
                "time AND gave a specific slot')."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Check the end-call branches are consolidated and each has an "
                "unambiguous trigger, so the agent doesn't end the call early or "
                "pick the wrong end node."
            ),
        ),
    },
    content="""\
Plan for multiple end-call scenarios but consolidate them into two or three
tool calls, not ten. A common pattern:

- end_call — successful completion, voicemail detection, wrong number, or hard
  disqualification.
- end_call_rescheduled — the caller asks for a different time and provides a
  specific slot.
- end_call_transfer — transfer to a human.

Each end-call tool needs a clear trigger condition in the prompt: "Call
end_call_rescheduled only if the user has explicitly asked to be called back
and provided a date and time." Ambiguous triggers cause the agent to end the
call early or route to the wrong end node.

These triggers are part of the node's success criteria — keep the full
decision tree in the success-criteria section and make sure each end-call
branch's condition is precise and mutually distinct.
""",
    audit_checks=(
        AuditCheck(
            id="end_calls_have_clear_triggers",
            judge_question=(
                "Does each end-call path in the prompt have a clear, specific "
                "trigger condition (rather than a vague 'end the call when done')?"
            ),
            expected="yes",
            quote=(
                "End-call trigger is vague — state the exact condition for each "
                "end-call branch so the agent doesn't hang up early or pick wrong."
            ),
        ),
    ),
    cross_refs=("success_criteria", "tool_calls"),
)
