"""Topic: read back critical info char-by-char; don't interrogate on casual details."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="readback_and_extraction",
    title="Read back critical info character-by-character; trust casual details",
    severity="high",
    applies_to_node_types=("agentNode", "startCall"),
    stages={
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "Instruct the agent to read critical values (email, order ID, phone, "
                "confirmation code) back character-by-character, and to do an "
                "explicit readback on super-critical confirmations (bookings, "
                "payment amounts). Tell it NOT to read back casual details."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Check the prompt verifies the values that hurt when wrong and "
                "doesn't turn every detail into a confirmation — reading back "
                "everything makes the call feel like an interview."
            ),
        ),
    },
    content="""\
Decide what's critical and verify only that. Over-confirming turns a call into
an interview; under-confirming books the wrong appointment.

Read back critical values character by character. For email addresses, order
IDs, phone numbers, and confirmation codes, repeat each character: "So your
email is S A M at gmail dot com, is that right?" If the caller says it's wrong,
ask them to spell it back to you character by character.

Do an explicit readback for super-critical confirmations — appointment slots,
payment amounts, scheduled callbacks: "Okay, so you want me to book you for
tomorrow at 8 AM, right?" Wait for the confirmation before acting on it.

Trust the transcript on casual details — name pronunciation, location,
retirement status, and the like. Reading every detail back is what makes an
agent feel robotic and slow.

Keep the mechanics of extraction (what to store, in which variable) in the
node's separate extraction_prompt field. This topic is about the spoken
confirmation behavior — what the agent says out loud to make sure it heard
right — not about where the value gets stored. When a value is read back as
digits (a phone number, a dollar amount), say it in spoken, grouped form — see
the numbers/dates/money topic.

Examples (prompt → behavior):
- Good: "Read the order ID back one character at a time and wait for the caller
  to confirm before looking it up."
- Good: "Don't read back the caller's city or how they pronounce their name —
  just continue."
- Bad:  "Confirm every detail the caller gives." (Interrogation; kills pace.)
""",
    audit_checks=(
        AuditCheck(
            id="reads_back_critical_values",
            judge_question=(
                "When the node captures a high-stakes value (email, order ID, phone "
                "number, confirmation code, booking, or payment amount), does the "
                "prompt instruct the agent to confirm it — character-by-character or "
                "via an explicit readback — before acting on it?"
            ),
            expected="yes",
            quote=(
                "Critical value isn't confirmed — read emails/IDs/amounts back "
                "before acting so a mis-hear doesn't propagate."
            ),
        ),
    ),
    cross_refs=("numbers_dates_money", "speech_handling", "call_flow_design"),
)
