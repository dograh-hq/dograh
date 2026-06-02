"""Topic: end every prompt with explicit success criteria."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="success_criteria",
    title="End each prompt with explicit success criteria",
    severity="high",
    applies_to_node_types=("agentNode", "startCall", "endCall"),
    stages={
        Stage.plan: StageLens(
            relevant=True,
            lens=(
                "Define exit and branch conditions up front: which tool ends the "
                "call, which fires on qualification, which reschedules. These become "
                "each node's success criteria and the edge conditions between nodes."
            ),
        ),
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "End each node prompt with a success-criteria section naming which "
                "tool to call under which condition (e.g. 'call schedule_appointment "
                "only after all three screening questions pass')."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Confirm every prompt that can trigger a tool or branch has explicit "
                "success criteria. Vague conditions are the top cause of wrong-tool "
                "and wrong-branch routing."
            ),
        ),
    },
    content="""\
Always end the prompt with a clear success-criteria section. This is what the
model uses to decide what counts as a good turn and which tool to call when.
Without it the model wanders; with it the model has a decision tree for the
tool-call space.

Spell out each branch as a condition → action:

  ## Success Criteria
  - Call schedule_appointment only after the user passes all three screening
    questions.
  - Call end_call if the user is disqualified, not interested, voicemail, or a
    wrong number.
  - Call end_call_rescheduled if the user wants a different time and has given a
    specific slot.

State each condition precisely — "after all three screening questions pass",
not "when qualified". These conditions also align with the edge conditions
between nodes, so a clear success-criteria section makes routing reliable.

This is closely tied to the tool-calls topic (which owns how individual tools
behave) and end-call logic (which owns the end-of-call branches). Success
criteria is the per-node summary that ties those decisions together.
""",
    audit_checks=(
        AuditCheck(
            id="has_explicit_success_criteria",
            judge_question=(
                "Does the prompt state, with specific conditions, when the agent "
                "should make each tool call or move to the next step — rather than "
                "leaving the decision implicit?"
            ),
            expected="yes",
            quote=(
                "No explicit success criteria — name which tool fires under which "
                "condition so the model doesn't wander."
            ),
        ),
    ),
    cross_refs=("tool_calls", "end_call_logic", "turn_taking"),
)
