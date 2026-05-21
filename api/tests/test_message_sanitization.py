from __future__ import annotations

from copy import deepcopy

from pipecat.utils.context.message_sanitization import (
    strip_thought_from_id,
    strip_thought_ids_from_messages,
)


def test_strip_thought_from_id():
    assert strip_thought_from_id("call_123__thought__abc") == "call_123"
    assert strip_thought_from_id("call_123") == "call_123"
    assert strip_thought_from_id(None) is None


def test_strip_thought_ids_from_messages_does_not_mutate_input():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1__thought__hidden",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1__thought__hidden",
            "content": '{"status":"ok"}',
        },
    ]
    original = deepcopy(messages)

    cleaned = strip_thought_ids_from_messages(messages)

    assert messages == original
    assert cleaned is not messages
    assert cleaned[0]["tool_calls"][0]["id"] == "call_1"
    assert cleaned[1]["tool_call_id"] == "call_1"
