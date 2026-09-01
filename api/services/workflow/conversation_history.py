"""Normalize a Pipecat LLM context for out-of-band analysis."""

from __future__ import annotations

import json
from typing import Any

from pipecat.processors.aggregators.llm_context import LLMContext

_TOOL_RESPONSE_STRIP_KEYS = {"status", "status_code"}
_TOOL_MESSAGE_MAX_CHARS = 2000
_TRANSITION_RESPONSE = '{"status": "done"}'


def _build_tool_call_name_lookup(context: LLMContext) -> dict[str, str]:
    """Build a mapping of tool-call IDs to their function names."""
    lookup: dict[str, str] = {}
    for message in context.messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls", []) or []:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = tool_call.get("id")
            function = tool_call.get("function") or {}
            if not isinstance(function, dict):
                continue
            function_name = function.get("name")
            if tool_call_id and function_name:
                lookup[tool_call_id] = function_name
    return lookup


def _format_tool_call(tool_call: object) -> str | None:
    """Format a tool invocation, including the parameters sent to it."""
    if not isinstance(tool_call, dict):
        return None

    function = tool_call.get("function") or {}
    if not isinstance(function, dict):
        return None

    tool_name = function.get("name") or "unknown"
    raw_arguments = function.get("arguments")
    if raw_arguments in (None, ""):
        formatted = "{}"
    else:
        try:
            parsed = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else raw_arguments
            )
            formatted = json.dumps(parsed, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError, ValueError):
            formatted = str(raw_arguments)

    if len(formatted) > _TOOL_MESSAGE_MAX_CHARS:
        formatted = formatted[:_TOOL_MESSAGE_MAX_CHARS] + "...(truncated)"

    return f"[Tool Call: {tool_name}]\nArguments: {formatted}"


def _format_tool_response(raw_content: str, tool_name: str) -> str | None:
    """Clean and bound a tool response for inclusion in analysis context."""
    if raw_content.strip() == _TRANSITION_RESPONSE:
        return f"[Tool Response: {tool_name}]\nResponse: completed"

    try:
        parsed = json.loads(raw_content)
        if isinstance(parsed, dict):
            if "data" in parsed:
                parsed = parsed["data"]
            else:
                for key in _TOOL_RESPONSE_STRIP_KEYS:
                    parsed.pop(key, None)
            formatted = json.dumps(parsed, ensure_ascii=False)
        else:
            formatted = raw_content
    except (json.JSONDecodeError, TypeError):
        formatted = raw_content

    if len(formatted) > _TOOL_MESSAGE_MAX_CHARS:
        formatted = formatted[:_TOOL_MESSAGE_MAX_CHARS] + "...(truncated)"

    return f"[Tool Response: {tool_name}]\nResponse: {formatted}"


def _get_role_and_content(message: Any) -> tuple[str | None, str | None]:
    """Return a normalized role and textual content from a context message."""
    if isinstance(message, dict):
        role = message.get("role")
        content = message.get("content")

        if isinstance(content, str):
            return role, content
        if isinstance(content, list):
            texts = [
                segment.get("text", "")
                for segment in content
                if isinstance(segment, dict) and segment.get("type") == "text"
            ]
            return role, " ".join(texts) if texts else None
        return role, None

    role = getattr(message, "role", None)
    parts = getattr(message, "parts", None)
    if role is None or parts is None:
        return None, None

    normalized_role = "assistant" if role == "model" else role
    texts = [text for part in parts if (text := getattr(part, "text", None))]
    return normalized_role, " ".join(texts) if texts else None


def build_conversation_history(context: LLMContext) -> str:
    """Render conversation text, tool calls, and relevant responses as text."""
    tool_call_names = _build_tool_call_name_lookup(context)
    lines: list[str] = []

    for message in context.messages:
        role, content = _get_role_and_content(message)
        if role in ("assistant", "user") and content:
            lines.append(f"{role}: {content}")

        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for tool_call in message.get("tool_calls", []) or []:
                formatted = _format_tool_call(tool_call)
                if formatted:
                    lines.append(formatted)
        elif message.get("role") == "tool":
            tool_content = message.get("content", "")
            tool_call_id = message.get("tool_call_id", "")
            tool_name = tool_call_names.get(tool_call_id, "unknown")
            formatted = _format_tool_response(tool_content, tool_name)
            if formatted:
                lines.append(formatted)

    return "\n".join(lines)


def has_user_turns(context: LLMContext) -> bool:
    """Return whether the context contains non-empty user speech."""
    return any(
        role == "user" and content
        for role, content in (
            _get_role_and_content(message) for message in context.messages
        )
    )
