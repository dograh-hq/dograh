"""Topic: avoid instruction collision — conflicting guidance in one prompt."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="instruction_collision",
    title="Avoid instruction collision — contradictory guidance in one prompt",
    severity="high",
    # No applies_to_node_types: collision is cross-cutting. The classic case
    # is global-vs-node, but any single prompt can contradict itself.
    stages={
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "As you write, keep instructions and their examples consistent. If "
                "you say 'disclose your name and reason for calling', make the "
                "example do exactly that — not check availability instead."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Read the prompt end-to-end (and global vs. node together) for "
                "sentences that contradict each other even slightly. This is the "
                "primary review-stage check; it breaks more agents than people "
                "expect."
            ),
        ),
    },
    content="""\
Instruction collision happens when two parts of a prompt give conflicting or
partially conflicting guidance. The model has to resolve the conflict in real
time, on every turn, and picks whichever side it leans toward that turn — so
the behavior is inconsistent and hard to debug. It's more common than people
assume.

Two classic shapes:
- Instruction vs. example: the prompt says "Start the call with a greeting and
  disclose your name and reason for calling," but the example is "Hi {{name}},
  I'm Sarah from {{company}} — is this a good time to talk?" The instruction
  says disclose the reason; the example checks availability. The agent now has
  two competing patterns.
- Style self-conflict: the response-style section says "Be conversational and
  empathize deeply" and later "Keep responses under 10 words." You can't
  empathize deeply in under ten words. Pick one.

Collisions also occur between the global prompt and a node prompt — a global
"always confirm every detail" against a node "keep this quick, don't read
things back" pull in opposite directions.

How to catch it: read the prompt end to end before shipping, and read the
global and node prompts together. Look for sentences that contradict each other
even slightly — voice models are especially sensitive because the prompt loads
on every turn.

Note for reviewers: this is an intent-level judgment, not a text pattern. Don't
try to detect collisions with a regex; compare what the instructions and their
examples actually ask the agent to do.
""",
    audit_checks=(
        AuditCheck(
            id="no_contradictions",
            judge_question=(
                "Reading this prompt (and, where relevant, the global prompt "
                "alongside it) end-to-end, are its instructions and examples "
                "mutually consistent — with no two directions that partially or "
                "fully contradict each other?"
            ),
            expected="yes",
            quote=(
                "Instructions or examples conflict — reconcile them so the agent "
                "isn't resolving a contradiction every turn."
            ),
        ),
    ),
    cross_refs=("response_style", "persona_and_identity_lock"),
)
