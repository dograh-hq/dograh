"""Topic: guardrails — out-of-scope, abuse, and honesty non-negotiables."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="guardrails",
    title="Guardrails for out-of-scope, abuse, and fabrication",
    severity="high",
    applies_to_node_types=("globalNode",),
    stages={
        Stage.plan: StageLens(
            relevant=True,
            lens=(
                "Decide the agent's scope boundaries: what's in scope, what to "
                "deflect, and when a call should end (sustained abuse, out-of-scope "
                "insistence). These become global guardrails."
            ),
        ),
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "In the global prompt, add guardrails: redirect out-of-scope queries "
                "to the call's purpose, handle abuse (warn, then end on repeat), and "
                "never fabricate information."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Confirm guardrails exist for out-of-scope queries, abusive callers, "
                "and fabrication. Missing guardrails surface in production as "
                "off-topic rambles, baited agents, or invented prices."
            ),
        ),
    },
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

The permanent-role lock and "never reveal the prompt / internal policies" rule
are closely related but live in the persona-and-identity-lock topic — keep that
clause there and reference it rather than restating it here.

Example:
- Good: "If asked anything outside helping with the caller's order, say you can
  only help with that and steer back. If the caller is abusive, warn once, then
  end the call on a second instance. Never make up order details — if you don't
  know, say so."
""",
    audit_checks=(
        AuditCheck(
            id="has_out_of_scope_and_abuse",
            judge_question=(
                "Does the prompt tell the agent how to handle out-of-scope or "
                "abusive input — redirecting to the call's purpose and de-escalating "
                "or ending on abuse — rather than leaving it open?"
            ),
            expected="yes",
            quote=(
                "No out-of-scope/abuse handling — agents without it drift off-topic "
                "or get baited."
            ),
        ),
        AuditCheck(
            id="forbids_fabrication",
            judge_question=(
                "Does the prompt instruct the agent not to fabricate information and "
                "to admit when it doesn't know something?"
            ),
            expected="yes",
            quote=(
                "Add a 'never fabricate — say so if you don't know' rule; agents "
                "invent prices and policies without it."
            ),
        ),
    ),
    cross_refs=("persona_and_identity_lock",),
)
