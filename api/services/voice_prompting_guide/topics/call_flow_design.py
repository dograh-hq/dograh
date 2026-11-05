"""Topic: structure node prompts in sections; sequence multi-turn tasks."""

from api.services.voice_prompting_guide._base import VoicePromptingTopic

TOPIC = VoicePromptingTopic(
    id="call_flow_design",
    title="Structure node prompts; sequence multi-turn tasks; design conversation around variable extraction",
    content="""\
A good node prompt is broken into clear sections — pick five to eight depending
on the use case rather than dumping one wall of text. Sections worth using:
main task at this node, call flow at this node, common objections, knowledge base, 
guardrails, rules, and success criteria.

For multi-turn tasks, break the work into a numbered sequence inside the call
flow. A refund-status flow looks like:
  1. Get the caller's name.
  2. Ask for the order ID.
  3. Verify the order ID character by character.
  4. Call get_order_details with orderId and name.
  5. Read back the order status.
  6. Ask if they need anything else.

Remember, the goal of this call is to collect information so design the questions
and flow which makese a coherent sense to a user.

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
)
