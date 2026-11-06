"""Shared business handoffs for builder integration tests."""

BRIEF = {
    "goal": "Greet callers and collect their name",
    "direction": "inbound",
    "information_to_collect": ["Caller name"],
    "success_criteria": ["The caller's name is collected"],
}
READY_REVIEW = {"verdict": "ready", "summary": "The workflow implements the brief."}
