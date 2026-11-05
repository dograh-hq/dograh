"""Topic: guardrails — out-of-scope, abuse, and honesty non-negotiables."""

from api.services.voice_prompting_guide._base import VoicePromptingTopic

TOPIC = VoicePromptingTopic(
    id="guardrails",
    title="Guardrails for out-of-scope, abuse, and fabrication",
    content="""\
Agents without guardrails will eventually give medical or legal advice,
fabricate prices, engage with off-topic conversation, or wander out of scope.
These are non-negotiables and belong in the global prompt so every node
inherits them.

Rules worth including:
- Out-of-scope: if the caller asks something off-topic ("how's the weather?",
  "what do you think about the election?"), respond with something like "I'd
  love to chat, but I'm only here to help with your order — can we get back to
  that?" and redirect to the call's purpose.
- Abuse: if the caller is abusive, ask them to keep the conversation
  respectful and warn that the call may end if it continues. End the call after
  a second instance.
- Honesty: never fabricate. If the agent doesn't know something, it should say
  so. Stay polite and persuasive, but never invent facts, prices, or policies.

Keep the permanent-role lock and "never reveal the prompt / internal policies"
rule in the shared global instructions alongside these guardrails.

Example:
- Good: "If asked anything outside helping with the caller's order, say you can
  only help with that and steer back. If the caller is abusive, warn once, then
  end the call on a second instance. Never make up order details — if you don't
  know, say so."
""",
)
