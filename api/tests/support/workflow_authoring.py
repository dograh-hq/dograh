"""Shared business handoffs for builder integration tests."""

BRIEF = {
    "goal": "Greet callers and collect their name",
    "direction": "inbound",
    "information_to_collect": ["Caller name"],
    "success_criteria": ["The caller's name is collected"],
}
PLAN = {
    "content": (
        "Welcome (startCall): Greet the caller. Continue to Collect name when ready. "
        "Collect name (agentNode): Ask for the caller's name and extract caller_name "
        "as a string. Continue to Goodbye once collected or if the caller declines. "
        "Goodbye (endCall): Thank the caller and say goodbye."
    ),
}
READY_REVIEW = {"verdict": "ready", "summary": "The workflow implements the brief."}
