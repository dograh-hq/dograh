"""Sanitize Gemini message history to prevent turn-order violations.

Vertex AI and Gemini reject completions where an assistant turn containing
tool_calls is immediately followed by a user text turn instead of a tool
response turn. This happens when a call is interrupted mid-tool execution.
The sanitizer injects synthetic tool response messages so the sequence is
always: assistant(tool_calls) -> tool(response) -> user(text).
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
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return messages with synthetic tool responses injected where needed.

    If an assistant turn with tool_calls is followed by a non-tool user turn,
    inserts a synthetic tool response per pending call before the user turn.
    Operates on the universal LLMContext dict format (role/content/tool_calls).
    """
    if not messages:
        return messages

    result: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        result.append(msg)

        if not _has_tool_calls(msg):
            continue

        next_msg = messages[i + 1] if i + 1 < len(messages) else None
        if next_msg is None or _is_tool_response(next_msg):
            continue

        # Next turn is not a tool response — inject one synthetic response per call.
        for tc in msg["tool_calls"]:
            tc_id = tc.get("id", "")
            name = tc.get("function", {}).get("name", "tool_call")
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": name,
                    "content": "interrupted_by_user",
                }
            )

    return result
