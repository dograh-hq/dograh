"""Topic: build human disfluencies into the agent's speech."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="disfluencies",
    title="Build natural disfluencies into the agent's speech",
    severity="medium",
    applies_to_node_types=("globalNode", "agentNode", "startCall"),
    stages={
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "Give the global prompt a disfluency vocabulary (fillers, thinking "
                "sounds, self-corrects, word repeats), target a couple per turn, and "
                "add a self-check: a perfectly polished sentence means it's drifted "
                "off-character."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Check the prompt actually instructs natural disfluency and includes "
                "the self-monitor. Polished-by-default speech is the tell that "
                "separates an agent from a person."
            ),
        ),
    },
    content="""\
LLMs default to clean, polished output. In text that reads well; in voice it's
the uncanny valley. Real people stutter, restart, use fillers, and self-correct
mid-thought. If the agent doesn't, callers notice even if they can't say why.

Build a disfluency vocabulary into the global prompt:
- Fillers: um, uh, like, so, well, you know, I mean
- Thinking sounds: let me see, hmm, one sec
- Self-corrects: "your order ID is - wait, let me check - okay, it's A X C one
  eight Z"
- Word repeats: "I can schedule that for - uh - for tomorrow at eight AM"

Target roughly two to four disfluencies per turn — at least one. Too few and
the agent sounds robotic; too many and it sounds glitchy. Add a self-monitoring
instruction: "If a turn comes out as one polished sentence with no disfluency,
you've drifted off-character."

When you give example phrases, write them as complete sample responses — the
model will reuse them closely. Pair that with a "vary your responses, don't
repeat the same sentence twice" rule so the samples don't get parroted.

This is a global-prompt rule whose effect lands on every spoken turn. It works
with the response-style topic (short, contraction-heavy turns are easier to
make sound human).
""",
    audit_checks=(
        AuditCheck(
            id="instructs_disfluency",
            judge_question=(
                "Does the prompt instruct the agent to speak with natural human "
                "disfluencies — fillers, self-corrections, or word repeats — rather "
                "than in consistently polished prose?"
            ),
            expected="yes",
            quote=(
                "No disfluency guidance — fully polished speech reads as robotic on "
                "a call."
            ),
        ),
    ),
    cross_refs=("response_style",),
)
