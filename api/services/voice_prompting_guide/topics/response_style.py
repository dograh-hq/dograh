"""Topic: short, spoken-style responses — write for the ear, not the eye."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="response_style",
    title="Keep responses short and spoken — write for the ear",
    severity="medium",
    applies_to_node_types=("globalNode", "agentNode", "startCall"),
    stages={
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "Add a response-style section to the global prompt: roughly 10-25 "
                "words per turn, two sentences max, contractions throughout, simple "
                "spoken English, and never more than three options at once. Tell it "
                "to vary phrasing so it doesn't sound robotic."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Check the style rules are present and don't contradict each other "
                "('empathize deeply' next to 'under 10 words' is an instruction "
                "collision)."
            ),
        ),
    },
    content="""\
Write for the ear, not the eye. A reply that reads well on screen is often too
long, too formal, or too list-like to sound right on a phone call.

The rules worth stating in the global prompt:
- Keep turns short: roughly 10-25 words, two sentences at most, unless the
  situation genuinely demands more.
- Use contractions everywhere — "I've", "you're", "we'll". The first time an
  agent says "I have" instead of "I've", the caller notices.
- Use simple, natural spoken English in full sentences, not clipped chatbot
  phrases. Prefer "Can you give me a ballpark number?" over "Ballpark is fine."
- Never offer more than three options at once. If you have five plan features,
  share two and ask if they want to hear more.
- Vary your phrasing. Models follow sample phrases closely and will overuse
  them; add a "don't repeat the same sentence twice" rule to keep it fresh.

This is a global-prompt concern that shapes every turn. It pairs with
disfluencies (how to sound human) and is the most common source of instruction
collision — a deep-empathy instruction sitting next to a hard word limit can't
both be satisfied. Keep the style section internally consistent.

Examples:
- Good: "Got it. Want me to text you the confirmation, or is email better?"
  (Short, contraction, one question, two options.)
- Bad:  "I would be more than happy to assist you with that request. Here are
  the following options available to you: ..." (Long, formal, list-shaped —
  reads fine, sounds wrong.)
""",
    audit_checks=(
        AuditCheck(
            id="constrains_length_and_register",
            judge_question=(
                "Does the prompt constrain responses to be short and spoken-style — "
                "roughly a sentence or two, contractions, simple conversational "
                "English — rather than long or formal?"
            ),
            expected="yes",
            quote=(
                "No length/register guidance — voice replies should be ~10-25 words, "
                "contractions, simple spoken English."
            ),
        ),
    ),
    cross_refs=("disfluencies", "instruction_collision", "language_and_format"),
)
