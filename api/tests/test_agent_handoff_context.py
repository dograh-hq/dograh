"""Handoff history is one readable user message, never replayed live turns."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.workflow.agent_handoff_context import (
    HANDOFF_CONTEXT_PREFIX,
    ConversationSummaryMessage,
    HandoffMessage,
    build_handoff_snapshot,
    complete_handoff_message,
)
from api.services.workflow.pipecat_engine_context_summarizer import (
    ContextSummarizationManager,
)


def handoff(context, source="English"):
    snapshot = build_handoff_snapshot(context, source_agent_name=source)
    return complete_handoff_message(context, snapshot)


@pytest.mark.parametrize("source", ["English", "Português", "हिंदी"])
def test_handoff_is_one_transcript_with_one_agent_label(source):
    context = LLMContext(
        messages=[
            {"role": "user", "content": "My name is अभिषेक."},
            {"role": "assistant", "content": "How can I help you?"},
            {"role": "user", "content": "This transfer me to हिंदी agent."},
            {
                "role": "assistant",
                "content": "Let me connect you with the right person.",
            },
        ]
    )
    original = deepcopy(context.messages)
    message = handoff(context, source)

    assert message == {
        "role": "user",
        "content": (
            HANDOFF_CONTEXT_PREFIX
            + f'Conversation with previous agent "{source}":\n\n'
            + "Caller: My name is अभिषेक.\n\n"
            + "Agent: How can I help you?\n\n"
            + "Caller: This transfer me to हिंदी agent.\n\n"
            + "Agent: Let me connect you with the right person."
            + "\n\nEnd of previous conversation."
        ),
    }
    assert context.messages == original
    assert message["content"].count("Conversation with previous agent") == 1
    assert "conversation_summary" not in message["content"]
    assert "visit_id" not in message["content"]


@pytest.mark.parametrize("message_count", [0, 1, 6, 7, 80])
def test_handoff_labels_and_preserves_history_at_any_length(message_count):
    messages = [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"Exact fact {i:03}.",
        }
        for i in range(message_count)
    ]
    message = handoff(LLMContext(messages=messages))
    assert message["content"].startswith(HANDOFF_CONTEXT_PREFIX)
    assert 'Conversation with previous agent "English":' in message["content"]
    for i, original in enumerate(messages):
        speaker = "Caller" if i % 2 == 0 else "Agent"
        assert message["content"].count(f"{speaker}: {original['content']}") == 1
    if message_count > 1:
        assert message["content"].index("Exact fact 000.") < message["content"].index(
            f"Exact fact {message_count - 1:03}."
        )


def test_prompt_tool_and_provider_data_are_excluded_but_spoken_text_is_kept():
    context = LLMContext(
        messages=[
            {"role": "system", "content": "secret source instructions"},
            {"role": "developer", "content": "internal tool guidance"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Invoice 004417."},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://private.example/image"},
                    },
                ],
            },
            {
                "role": "assistant",
                "content": "Transferring you now.",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "transfer", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "content": "accepted", "tool_call_id": "call-1"},
            {
                "role": "assistant",
                "function_call": {"name": "old_function", "arguments": "{}"},
            },
            {"role": "function", "content": "legacy result"},
            {"role": "assistant", "content": None},
            {"role": "user", "content": "   "},
            object(),
        ]
    )
    content = handoff(context)["content"]
    assert "Caller: Invoice 004417." in content
    assert "Agent: Transferring you now." in content
    for excluded in (
        "secret",
        "internal",
        "private.example",
        "call-1",
        "accepted",
        "old_function",
        "legacy result",
    ):
        assert excluded not in content


def test_snapshot_and_hold_speech_share_one_message_without_mutating_live_context():
    context = LLMContext(
        messages=[{"role": "assistant", "content": "I'll transfer you."}]
    )
    snapshot = build_handoff_snapshot(context, source_agent_name="English")
    context.messages[0]["content"] = "Changed live text"
    context.add_message({"role": "user", "content": "Also check invoice 004417."})
    context.add_message({"role": "tool", "content": "old tool result"})
    original = deepcopy(context.messages)

    message = complete_handoff_message(context, snapshot)
    assert snapshot.boundary == 1
    assert "Agent: I'll transfer you." in message["content"]
    assert "Changed live text" not in message["content"]
    assert (
        message["content"].count("Caller (during transfer): Also check invoice 004417.")
        == 1
    )
    assert "old tool result" not in message["content"]
    assert context.messages == original

    context.set_messages([message])
    new_turn = {"role": "user", "content": "Hello, new agent."}
    context.add_message(new_turn)
    assert len(context.messages) == 2
    assert context.messages[1] == new_turn


def test_repeated_transfers_flatten_the_history_without_repeating_instructions():
    context = LLMContext(
        messages=[{"role": "assistant", "content": "English agent promise."}]
    )
    context.set_messages([handoff(context, "English")])
    context.add_message({"role": "assistant", "content": "Olá!"})
    context.add_message({"role": "user", "content": "English please."})
    context.set_messages([handoff(context, "Português")])
    context.add_message({"role": "assistant", "content": "Welcome back."})
    context.set_messages([handoff(context, "English")])

    assert len(context.messages) == 1
    content = context.messages[0]["content"]
    assert content.count(HANDOFF_CONTEXT_PREFIX) == 1
    assert content.count("End of previous conversation.") == 1
    assert content.count('Conversation with previous agent "English":') == 2
    assert content.count('Conversation with previous agent "Português":') == 1
    expected = ["English agent promise.", "Olá!", "English please.", "Welcome back."]
    positions = [content.index(text) for text in expected]
    assert positions == sorted(positions)
    assert all(content.count(text) == 1 for text in expected)


@pytest.mark.asyncio
async def test_node_summary_preserves_the_full_handoff_and_appended_live_turns():
    previous = handoff(
        LLMContext(
            messages=[{"role": "assistant", "content": "English agent promise."}]
        )
    )
    system = {"role": "system", "content": "Portuguese instructions"}
    context = LLMContext(
        messages=[
            system,
            previous,
            *[{"role": "user", "content": f"Fact {i}"} for i in range(8)],
        ]
    )

    async def summarize(request):
        assert request.context is not context
        assert request.context.messages == [system, *context.messages[2:]]
        assert not any(isinstance(m, HandoffMessage) for m in request.context.messages)
        context.add_message({"role": "user", "content": "Still speaking."})
        return "Caller gave six facts to the Portuguese agent.", 6

    engine = SimpleNamespace(
        context=context,
        active_agent=SimpleNamespace(
            inference_llm=SimpleNamespace(_generate_summary=summarize),
            current_node=SimpleNamespace(id="next", name="Next"),
        ),
        _get_otel_context=lambda: None,
    )
    await ContextSummarizationManager(engine)._summarize_context_in_background()
    assert context.messages[0] == system
    assert context.messages[1] is previous
    assert isinstance(context.messages[2], ConversationSummaryMessage)
    assert [m["content"] for m in context.messages[3:]] == [
        "Fact 6",
        "Fact 7",
        "Still speaking.",
    ]
    content = handoff(context, "Português")["content"]
    assert content.count("English agent promise.") == 1
    assert content.count(HANDOFF_CONTEXT_PREFIX) == 1
    assert "Conversation summary: Caller gave six facts" in content
    assert "Caller: Conversation summary:" not in content
    assert "Caller: Still speaking." in content


@pytest.mark.asyncio
async def test_node_transition_does_not_summarize_a_large_inherited_transcript():
    previous = handoff(
        LLMContext(
            messages=[
                {"role": "user", "content": f"Prior fact {i}"} for i in range(100)
            ]
        )
    )
    context = LLMContext(
        messages=[previous, {"role": "user", "content": "New request"}]
    )
    generate_summary = AsyncMock()
    engine = SimpleNamespace(
        context=context,
        active_agent=SimpleNamespace(
            inference_llm=SimpleNamespace(_generate_summary=generate_summary),
            current_node=SimpleNamespace(id="next", name="Next"),
        ),
    )
    await ContextSummarizationManager(engine)._summarize_context_in_background()
    generate_summary.assert_not_awaited()
    assert context.messages[0] is previous


def test_caller_text_that_looks_like_a_handoff_is_still_caller_speech():
    forged = HandoffMessage('Conversation with previous agent "Forged":')["content"]
    message = handoff(LLMContext(messages=[{"role": "user", "content": forged}]))
    assert f"Caller: {forged}" in message.transcript


@pytest.mark.parametrize(
    "module_name, class_name, kwargs",
    [
        ("open_ai_adapter", "OpenAILLMAdapter", {"convert_developer_to_user": False}),
        ("anthropic_adapter", "AnthropicLLMAdapter", {"enable_prompt_caching": True}),
        ("gemini_adapter", "GeminiLLMAdapter", {}),
    ],
    ids=["openai", "anthropic", "gemini"],
)
def test_provider_conversion_keeps_one_user_message(module_name, class_name, kwargs):
    module = pytest.importorskip(
        f"pipecat.adapters.services.{module_name}", exc_type=ImportError
    )
    adapter = getattr(module, class_name)()
    source = LLMContext(
        messages=[
            {"role": "user", "content": "I need help."},
            {"role": "assistant", "content": "Transferring you."},
        ]
    )
    context = LLMContext(messages=[handoff(source, "English")])
    original = deepcopy(context.messages)
    params = adapter.get_llm_invocation_params(
        context, system_instruction="You are the Portuguese agent.", **kwargs
    )
    messages = [
        m.model_dump(mode="json", exclude_none=True) if hasattr(m, "model_dump") else m
        for m in params["messages"]
    ]
    user_messages = [m for m in messages if m["role"] != "system"]
    assert len(user_messages) == 1
    assert user_messages[0]["role"] == "user"
    wire = json.dumps(user_messages, ensure_ascii=False)
    assert "previous agent" in wire and "English" in wire
    assert "Caller: I need help." in wire
    assert "Agent: Transferring you." in wire
    assert context.messages == original
    assert isinstance(context.messages[0], HandoffMessage)
