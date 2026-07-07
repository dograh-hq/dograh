"""Tests for sanitize_gemini_history.

Operates on the universal LLMContext dict format (role/content/tool_calls).
No pipecat import required — the sanitizer is a pure dict transform.
"""

import pytest

from api.services.pipecat.gemini_history_sanitizer import sanitize_gemini_history


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant_text(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _assistant_tool_call(name: str, call_id: str = "call_1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": "{}"}}],
    }


def _tool_response(name: str, call_id: str = "call_1") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": "done"}


def test_passthrough_when_no_tool_calls():
    msgs = [_user("hello"), _assistant_text("hi")]
    assert sanitize_gemini_history(msgs) == msgs


def test_passthrough_when_tool_response_follows():
    msgs = [
        _user("run it"),
        _assistant_tool_call("end_call"),
        _tool_response("end_call"),
        _user("thanks"),
    ]
    result = sanitize_gemini_history(msgs)
    assert result == msgs


def test_injects_synthetic_response_before_user_text():
    msgs = [
        _user("run it"),
        _assistant_tool_call("end_call", "call_1"),
        _user("interrupted"),
    ]
    result = sanitize_gemini_history(msgs)
    assert len(result) == 4
    assert result[2]["role"] == "tool"
    assert result[2]["tool_call_id"] == "call_1"
    assert result[2]["name"] == "end_call"
    assert result[2]["content"] == "interrupted_by_user"
    assert result[3] == _user("interrupted")


def test_injects_multiple_responses_for_parallel_tool_calls():
    msgs = [
        _user("go"),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "function": {"name": "tool_a", "arguments": "{}"}},
                {"id": "c2", "function": {"name": "tool_b", "arguments": "{}"}},
            ],
        },
        _user("interrupted"),
    ]
    result = sanitize_gemini_history(msgs)
    assert len(result) == 5
    assert result[2]["tool_call_id"] == "c1"
    assert result[2]["name"] == "tool_a"
    assert result[3]["tool_call_id"] == "c2"
    assert result[3]["name"] == "tool_b"
    assert result[4] == _user("interrupted")


def test_handles_llm_specific_message_objects_passthrough():
    """Non-dict messages (LLMSpecificMessage) pass through without error."""

    class FakeSpecific:
        pass

    specific = FakeSpecific()
    msgs = [_user("hi"), specific, _user("bye")]
    result = sanitize_gemini_history(msgs)
    assert result == msgs


def test_empty_messages():
    assert sanitize_gemini_history([]) == []


def test_does_not_mutate_input():
    msgs = [
        _assistant_tool_call("end_call", "c1"),
        _user("interrupted"),
    ]
    original_len = len(msgs)
    sanitize_gemini_history(msgs)
    assert len(msgs) == original_len
