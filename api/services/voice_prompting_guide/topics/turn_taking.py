"""Topic: end every agent turn with a question or clear nudge."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="turn_taking",
    title="End every agent turn with a question or clear nudge",
    severity="high",
    applies_to_node_types=("globalNode", "agentNode", "startCall"),
    stages={
        Stage.plan: StageLens(
            relevant=True,
            lens=(
                "When sketching the flow, plan a clear handoff back to the user at "
                "each node. Nodes that finish without prompting the user are stall "
                "risks; flag them at design time."
            ),
        ),
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "Instruct the agent to ask, confirm, or wait for the user at the end "
                "of every turn. If no natural question fits, add a clarifier "
                "('Does that work?', 'Make sense?')."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Check each prompt instructs the agent to ask or wait. Don't look "
                "for a literal '?' — the prompt is meta-instruction, not script."
            ),
        ),
    },
    content="""\
End every agent turn with a question or a clear prompt for the user to respond.

Why this matters: if the agent finishes speaking without prompting the user,
both sides go silent. The agent waits for user input; the user has no signal
that it's their turn. Calls stall, then drop.

How to write prompts that produce this behavior:
- Instruct the agent to ask, confirm, find out, or wait at the end of each
  turn. Verbs that imply a handoff are what matter.
- When the agent has just acknowledged something (e.g. the user shared a
  personal detail), tell it to acknowledge briefly and then return to the
  agenda with a question.
- When the agent has completed an action with nothing meaningful left to
  ask, instruct it to add a clarifier — "Does that work?", "Make sense?",
  "Anything else?" — and wait.

Important caveat: this rule applies to the *runtime behavior* the prompt is
meant to produce, not to the literal text of the prompt itself. A prompt
like "Greet the user warmly. Ask if it's a good time to talk." contains no
'?' but will produce a question at runtime. Do not enforce this rule with a
regex over prompt text — it would false-fire on well-written prompts.

Examples (prompt → expected runtime behavior):
- Good: "Greet the user using {{first_name}}. Ask if it's a good time to talk."
- Good: "Read back the appointment slot. Wait for the user to confirm or
  pick a different time."
- Bad:  "Thank the user. End the call." (No handoff cue — risks dead air
  before the end-call tool fires.)
""",
    audit_checks=(
        AuditCheck(
            id="instructs_ask_or_wait",
            judge_question=(
                "Does this prompt instruct the agent to ask a question, request "
                "input, or wait for the user before continuing? A direct "
                "instruction to ask, find out, confirm, or await counts as yes."
            ),
            expected="yes",
            quote=(
                "Prompt doesn't instruct the agent to ask or wait — risks both "
                "parties going silent."
            ),
        ),
    ),
    cross_refs=("success_criteria", "response_style"),
)
