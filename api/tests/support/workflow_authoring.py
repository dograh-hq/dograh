"""Shared business handoffs for builder integration tests."""

BRIEF = {
    "goal": "Greet callers and collect their name",
    "audience": "Customers",
    "direction": "inbound",
    "trigger": "An incoming phone call",
    "information_to_collect": ["Caller name"],
    "conversation_steps": ["Greet the caller", "Ask their name", "Say goodbye"],
    "success_criteria": ["The caller's name is collected"],
    "exit_conditions": ["The caller is ready to end"],
    "acceptance_scenarios": ["A caller gives their name and the agent says goodbye"],
}
READY_REVIEW = {"verdict": "ready", "summary": "The workflow implements the brief."}
