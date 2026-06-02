"""Topic: handle noisy audio, bad transcripts, and silence gracefully."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="speech_handling",
    title="Handle noisy audio and bad transcripts without guessing",
    severity="medium",
    applies_to_node_types=("globalNode",),
    stages={
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "Tell the global prompt that audio is noisy and transcripts may be "
                "wrong. When a response doesn't make coherent sense, the agent "
                "should ask the caller to repeat rather than guess."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Confirm the prompt acknowledges noisy transcripts and gives a "
                "recovery move ('Sorry, can you repeat that?'). Agents that guess at "
                "garbled input compound the error."
            ),
        ),
    },
    content="""\
Voice transcripts are noisy. Transcripts arrive partially wrong, callers talk
over the agent, lines drop, and accents confuse the STT — and you can't ask the
caller to "scroll up". The prompt has to handle this without breaking flow.

Put in the global prompt:
- Tell the model the audio can be noisy and the transcript may contain errors.
- When the user's response doesn't make coherent sense — likely a transcript
  error — the agent should say something like "Sorry, can you repeat that?" or
  "The line's a bit patchy, I didn't catch you" rather than guessing at what
  was said.

This is the input-side complement to reading back critical information: speech
handling covers what to do when you didn't catch something; readback covers
confirming the things you did catch but can't afford to get wrong.

Examples:
- Good: "Audio may be noisy and transcripts imperfect. If a reply doesn't make
  sense, ask the caller to repeat instead of assuming."
- Bad:  Agent receives a garbled order ID and proceeds to a tool call with its
  best guess, producing a wrong-order lookup.
""",
    audit_checks=(
        AuditCheck(
            id="handles_unclear_input",
            judge_question=(
                "Does the prompt tell the agent what to do when the caller's input "
                "is unclear or incoherent — ask them to repeat — rather than "
                "guessing at the meaning?"
            ),
            expected="yes",
            quote=(
                "No recovery for unclear input — tell the agent to ask the caller to "
                "repeat instead of guessing at a bad transcript."
            ),
        ),
    ),
    cross_refs=("readback_and_extraction", "language_and_format"),
)
