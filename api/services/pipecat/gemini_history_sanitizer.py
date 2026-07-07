"""Sanitize Gemini message history to prevent turn-order violations.

Vertex AI and Gemini reject completions where an assistant turn containing
tool_calls is immediately followed by a user text turn instead of a tool
response turn. This happens when a call is interrupted mid-tool execution.
The sanitizer injects synthetic tool response messages so every pending
tool_call_id has a response before the next user text turn.
"""

from __future__ import annotations

from typing import Any


def _has_tool_calls(msg: Any) -> bool:
    if not isinstance(msg, dict):
        return False
    return bool(msg.get("role") == "assistant" and msg.get("tool_calls"))


def _is_tool_response(msg: Any) -> bool:
    if not isinstance(msg, dict):
        return False
    return msg.get("role") == "tool"


def sanitize_gemini_history(
    messages: list[Any],
) -> list[Any]:
    """Return messages with synthetic tool responses injected where needed.

    For each assistant turn with tool_calls, walks the immediately following
    tool response turns to collect which call IDs are already answered. Any
    unanswered call IDs are injected as synthetic tool responses
    (content: "interrupted_by_user") before the next non-tool turn.

    Handles:
    - Zero responses appended: inject all (fully interrupted)
    - Some responses appended: inject only the missing ones (partially interrupted)
    - All responses present: pass through unchanged

    Operates on the universal LLMContext dict format (role/content/tool_calls).
    Non-dict messages (LLMSpecificMessage) pass through unchanged.
    """
    if not messages:
        return messages

    result: list[Any] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        result.append(msg)
        i += 1

        if not _has_tool_calls(msg):
            continue

        # Collect IDs and names for all pending calls in this assistant turn.
        pending: dict[str, str] = {
            tc.get("id", ""): tc.get("function", {}).get("name", "tool_call")
            for tc in msg["tool_calls"]
        }

        # Consume consecutive tool response turns, tracking which IDs are answered.
        answered: set[str] = set()
        while i < len(messages) and _is_tool_response(messages[i]):
            answered.add(messages[i].get("tool_call_id", ""))
            result.append(messages[i])
            i += 1

        # Inject synthetics for any call IDs that have no response yet.
        for call_id, name in pending.items():
            if call_id not in answered:
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": "interrupted_by_user",
                    }
                )

    return result
