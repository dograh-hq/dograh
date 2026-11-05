"""Topic: end every prompt with explicit success criteria."""

from api.services.voice_prompting_guide._base import VoicePromptingTopic

TOPIC = VoicePromptingTopic(
    id="success_criteria",
    title="End each prompt with explicit success criteria",
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
)
