"""Topic: structure node prompts in sections; sequence multi-turn tasks."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="call_flow_design",
    title="Structure node prompts; sequence multi-turn tasks; ask one thing at a time",
    severity="medium",
    applies_to_node_types=("agentNode", "startCall"),
    stages={
        Stage.plan: StageLens(
            relevant=True,
            lens=(
                "For each multi-turn node, sketch the step sequence (e.g. get name → "
                "get order ID → verify → call tool → read back). Decide what each "
                "node collects — one item per turn."
            ),
        ),
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "Break the node prompt into 5-8 labeled sections and write multi-turn "
                "tasks as a numbered sequence. Collect one piece of information per "
                "turn, and keep variable-extraction instructions in the node's "
                "separate extraction_prompt field, not the main prompt."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Check the node asks for one thing at a time and that extraction "
                "logic isn't tangled into the conversational prompt."
            ),
        ),
    },
    content="""\
A good node prompt is broken into clear sections — pick five to eight depending
on the use case rather than dumping one wall of text. Sections worth using:
overall context & persona, main task at this node, call flow at this node,
response style, speech handling, common objections, knowledge base, guardrails,
rules, and success criteria.

For multi-turn tasks, break the work into a numbered sequence inside the call
flow. A refund-status flow looks like:
  1. Get the caller's name.
  2. Ask for the order ID.
  3. Verify the order ID character by character.
  4. Call get_order_details with orderId and name.
  5. Read back the order status.
  6. Ask if they need anything else.

Collect one thing at a time. Agents that ask "Can I get your name, date of
birth, and reason for calling?" almost always fail — the user gives one piece,
the agent has to chase the rest, and the flow falls apart. Sequencing one
question per turn is slower in theory but faster in practice because you never
have to recover from a half-answered batch.

Keep variable extraction out of the conversational prompt. Dograh gives each
agent/start/end node a separate `extraction_prompt` field — put the logic for
capturing a value there. The call flow can say "ask for the order ID"; the
rule for parsing and storing it belongs in extraction_prompt.

Generic, always-applicable material (persona, common objections, global
response style, anti-jailbreak rules) belongs in the global prompt, not in
each node prompt — a global node is reachable from anywhere in the call.
""",
    audit_checks=(
        AuditCheck(
            id="collects_one_thing_at_a_time",
            judge_question=(
                "When the node gathers multiple pieces of information, does the "
                "prompt instruct the agent to collect them one at a time rather than "
                "asking for several in a single turn?"
            ),
            expected="yes",
            quote=(
                "Prompt batches several asks in one turn — collect one item at a "
                "time, confirming as you go."
            ),
        ),
        AuditCheck(
            id="extraction_kept_separate",
            judge_question=(
                "Is the main conversational prompt free of variable-extraction "
                "instructions (which belong in the separate extraction_prompt "
                "field)?"
            ),
            expected="yes",
            quote=(
                "Extraction logic is mixed into the main prompt — move it to the "
                "node's extraction_prompt field."
            ),
        ),
    ),
    cross_refs=("success_criteria", "readback_and_extraction", "tool_calls"),
)
