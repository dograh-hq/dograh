"""Unit tests for OpenAILLMAdapter handling of empty tools and tool messages."""

from openai._types import NOT_GIVEN as OPENAI_NOT_GIVEN
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.adapters.services.open_ai_adapter import (
    OpenAILLMAdapter,
    openai_from_llm_context_tools,
)
from pipecat.processors.aggregators.llm_context import LLMContext


def test_openai_from_llm_context_tools_omits_empty_tools():
    """Verify that an empty list of tools is converted to OPENAI_NOT_GIVEN so the wire payload omits tools."""
    assert openai_from_llm_context_tools([]) == OPENAI_NOT_GIVEN
    assert openai_from_llm_context_tools(None) == OPENAI_NOT_GIVEN


def test_adapter_sanitizes_tool_messages_when_tools_is_empty():
    """Verify that orphaned tool messages and tool calls are flattened when tools is empty."""
    adapter = OpenAILLMAdapter()

    messages = [
        {"role": "system", "content": "You are a receptionist."},
        {"role": "user", "content": "Please end the call."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "transition_to_end_call", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "Transitioned successfully",
        },
        {"role": "user", "content": "Goodbye."},
    ]

    context = LLMContext(messages=messages, tools=ToolsSchema(standard_tools=[]))
    params = adapter.get_llm_invocation_params(context, convert_developer_to_user=True)

    # 1. Tools and tool_choice should be omitted
    assert params["tools"] == OPENAI_NOT_GIVEN
    assert params["tool_choice"] == OPENAI_NOT_GIVEN

    # 2. Messages should be normalized without tool_calls or role='tool'
    wire_messages = params["messages"]
    assert len(wire_messages) == 5

    for msg in wire_messages:
        assert msg["role"] != "tool", (
            "role='tool' must be normalized when tools is empty"
        )
        assert "tool_calls" not in msg, (
            "tool_calls must be stripped when tools is empty"
        )

    # Check that assistant message captured the action in content
    assert wire_messages[2]["role"] == "assistant"
    assert "[Called transition_to_end_call]" in wire_messages[2]["content"]

    # Check that tool message became user message with tool result
    assert wire_messages[3]["role"] == "user"
    assert "[Tool result]: Transitioned successfully" in wire_messages[3]["content"]

    # 3. Canonical source messages in memory must remain completely untouched
    assert messages[2]["role"] == "assistant"
    assert messages[2]["tool_calls"] is not None
    assert messages[2]["tool_calls"][0]["function"]["name"] == "transition_to_end_call"
    assert messages[3]["role"] == "tool"
    assert messages[3]["content"] == "Transitioned successfully"


def test_adapter_preserves_tool_messages_when_tools_are_present():
    """Verify that when tools are actively present, messages are passed through unchanged."""
    adapter = OpenAILLMAdapter()

    messages = [
        {"role": "user", "content": "Book an appointment."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "book", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "Booked",
        },
    ]

    from pipecat.adapters.schemas.tools_schema import FunctionSchema

    dummy_func = FunctionSchema(
        name="book",
        description="Book appointment",
        properties={},
        required=[],
    )
    context = LLMContext(
        messages=messages, tools=ToolsSchema(standard_tools=[dummy_func])
    )
    params = adapter.get_llm_invocation_params(context, convert_developer_to_user=True)

    # Tools should be present
    assert params["tools"] != OPENAI_NOT_GIVEN
    assert len(params["tools"]) == 1

    # Messages should preserve tool_calls and role='tool'
    wire_messages = params["messages"]
    assert wire_messages[1]["tool_calls"] is not None
    assert wire_messages[2]["role"] == "tool"


def test_adapter_handles_complex_content_types_when_sanitizing():
    """Verify that list content (multimodal) and None content are handled without errors."""
    adapter = OpenAILLMAdapter()

    messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Checking slot..."}],
            "tool_calls": [
                {
                    "id": "call_99",
                    "type": "function",
                    "function": {"name": "check_slot", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_99",
            "content": [{"type": "text", "text": "Available"}],
        },
    ]

    context = LLMContext(messages=messages, tools=ToolsSchema(standard_tools=[]))
    params = adapter.get_llm_invocation_params(context, convert_developer_to_user=True)

    wire_messages = params["messages"]
    assert wire_messages[0]["role"] == "assistant"
    assert "tool_calls" not in wire_messages[0]
    assert isinstance(wire_messages[0]["content"], list)
    assert wire_messages[1]["role"] == "user"
    assert wire_messages[1]["content"] == [{"type": "text", "text": "Available"}]


def test_adapter_sanitizes_unpaired_assistant_tool_call_without_tool_result():
    """Verify orphaned assistant tool_call with no matching tool response is sanitized."""
    adapter = OpenAILLMAdapter()

    messages = [
        {"role": "user", "content": "Cancel my booking."},
        {
            "role": "assistant",
            "content": "Cancelling now...",
            "tool_calls": [
                {
                    "id": "call_interrupted",
                    "type": "function",
                    "function": {"name": "cancel_booking", "arguments": "{}"},
                }
            ],
        },
        # No matching role='tool' message follows (e.g. user interrupted or pipeline transitioned)
        {"role": "user", "content": "Wait, never mind, just end the call."},
    ]

    context = LLMContext(messages=messages, tools=ToolsSchema(standard_tools=[]))
    params = adapter.get_llm_invocation_params(context, convert_developer_to_user=True)

    wire_messages = params["messages"]
    assert len(wire_messages) == 3

    # The lone assistant tool_call should be stripped and summarized into content
    assistant_msg = wire_messages[1]
    assert assistant_msg["role"] == "assistant"
    assert "tool_calls" not in assistant_msg
    assert "Cancelling now..." in assistant_msg["content"]
    assert "[Called cancel_booking]" in assistant_msg["content"]

    # Verify no tool messages in payload
    for msg in wire_messages:
        assert msg["role"] != "tool"

    # Canonical source messages in memory must remain completely untouched
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"] is not None
    assert len(messages[1]["tool_calls"]) == 1
    assert messages[1]["tool_calls"][0]["function"]["name"] == "cancel_booking"
    assert messages[1]["content"] == "Cancelling now..."


def test_adapter_sanitizes_unpaired_tool_message_without_assistant_call():
    """Verify orphaned tool result with no preceding assistant tool_call is sanitized."""
    adapter = OpenAILLMAdapter()

    messages = [
        # Preceding assistant call dropped (e.g. history sliding window / truncation)
        {
            "role": "tool",
            "tool_call_id": "call_dropped_from_history",
            "content": "Booking cancellation confirmed",
        },
        {"role": "user", "content": "Thanks, goodbye!"},
    ]

    context = LLMContext(messages=messages, tools=ToolsSchema(standard_tools=[]))
    params = adapter.get_llm_invocation_params(context, convert_developer_to_user=True)

    wire_messages = params["messages"]
    assert len(wire_messages) == 2

    # The lone role='tool' message should be converted to user
    orphan_tool_msg = wire_messages[0]
    assert orphan_tool_msg["role"] == "user"
    assert "[Tool result]: Booking cancellation confirmed" in orphan_tool_msg["content"]

    # Canonical source messages in memory must remain completely untouched
    assert messages[0]["role"] == "tool"
    assert messages[0]["content"] == "Booking cancellation confirmed"
    assert messages[0]["tool_call_id"] == "call_dropped_from_history"
