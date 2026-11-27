"""Topic: consolidate end-call scenarios with clear trigger conditions."""

from api.services.voice_prompting_guide._base import VoicePromptingTopic

TOPIC = VoicePromptingTopic(
    id="end_call_logic",
    title="Consolidate end-call scenarios; give each a clear trigger",
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
)
