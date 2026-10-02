"""Tests for integration knowledge added to the conversation before each turn.

Covers apply_integration_context: the latest user message goes to every
provider, their answers become one developer message after it, the next
turn replaces that message, a turn without knowledge removes it, the system
prompt is never touched, saved conversations leave the message out, and a
provider that fails or misses its time budget is skipped. Also covers the
engine clearing providers on End nodes and the knowledge staying behind on
an agent handoff.
"""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.pipecat import integration_context
from api.services.pipecat.integration_context import (
    IntegrationContextMessage,
    apply_integration_context,
    last_user_text,
    without_integration_context,
)
from api.services.workflow.agent_handoff_context import (
    conversation_messages,
    messages_after_boundary,
)
from api.services.workflow.pipecat_engine import PipecatEngine


def _agent(*providers):
    return types.SimpleNamespace(
        context_providers=list(providers),
        system_prompt="You are the support agent.",
        llm=types.SimpleNamespace(_update_settings=AsyncMock()),
    )


def _user(text):
    return {"role": "user", "content": text}


def _knowledge(context) -> list[str]:
    return [
        m["content"]
        for m in context.messages
        if isinstance(m, IntegrationContextMessage)
    ]


def test_last_user_text_reads_the_newest_user_message():
    messages = [
        _user("first question"),
        {"role": "assistant", "content": "an answer"},
        {"role": "user", "content": [{"type": "text", "text": " second question "}]},
        {"role": "assistant", "content": "thinking"},
    ]
    assert last_user_text(messages) == "second question"
    assert last_user_text([{"role": "assistant", "content": "hi"}]) == ""


async def test_knowledge_follows_the_user_message_as_a_developer_message():
    provider = AsyncMock(return_value="1. Refunds take 5 days.")
    agent = _agent(provider)
    context = LLMContext(messages=[_user("How long do refunds take?")])

    await apply_integration_context(agent, context)

    provider.assert_awaited_once_with("How long do refunds take?")
    assert context.messages[-1] == {
        "role": "developer",
        "content": "1. Refunds take 5 days.",
    }
    agent.llm._update_settings.assert_not_awaited()


async def test_next_turn_replaces_the_knowledge():
    provider = AsyncMock(
        side_effect=["1. Refunds take 5 days.", "1. We ship to 40 countries."]
    )
    agent = _agent(provider)
    context = LLMContext(messages=[_user("Refunds?")])

    await apply_integration_context(agent, context)
    context.add_message({"role": "assistant", "content": "Five days."})
    context.add_message(_user("Do you ship abroad?"))
    await apply_integration_context(agent, context)

    assert [m["role"] for m in context.messages] == [
        "user",
        "assistant",
        "user",
        "developer",
    ]
    assert _knowledge(context) == ["1. We ship to 40 countries."]


async def test_turn_without_knowledge_removes_the_previous_one():
    agent = _agent(AsyncMock(side_effect=["1. Refunds take 5 days.", None]))
    context = LLMContext(messages=[_user("a")])

    await apply_integration_context(agent, context)
    context.add_message(_user("b"))
    await apply_integration_context(agent, context)

    assert _knowledge(context) == []


async def test_agent_without_providers_leaves_the_conversation_alone():
    agent = _agent()
    context = LLMContext(messages=[_user("hi")])

    await apply_integration_context(agent, context)

    assert context.messages == [_user("hi")]


def test_saved_conversations_leave_the_knowledge_out():
    messages = [
        _user("Refunds?"),
        IntegrationContextMessage(role="developer", content="1. Refunds take 5 days."),
        {"role": "assistant", "content": "Five days."},
    ]
    assert without_integration_context(messages) == [messages[0], messages[2]]


async def test_failing_or_slow_provider_is_skipped(monkeypatch):
    monkeypatch.setattr(integration_context, "PROVIDER_TIMEOUT_SECONDS", 0.01)

    async def slow(_text):
        await asyncio.sleep(1)
        return "too late"

    broken = AsyncMock(side_effect=RuntimeError("boom"))
    working = AsyncMock(return_value="1. Store credit never expires.")
    context = LLMContext(messages=[_user("q")])

    await apply_integration_context(_agent(slow, broken, working), context)

    assert _knowledge(context) == ["1. Store credit never expires."]


@pytest.mark.parametrize("messages", [[], [_user("  ")]])
async def test_turn_without_user_text_asks_no_provider(messages):
    provider = AsyncMock(return_value="unused")
    context = LLMContext(messages=list(messages))

    await apply_integration_context(_agent(provider), context)

    provider.assert_not_awaited()
    assert _knowledge(context) == []


async def test_end_node_clears_the_providers(three_node_workflow):
    llm = Mock()
    llm._update_settings = AsyncMock()
    engine = PipecatEngine(workflow=three_node_workflow, llm=llm, call_context_vars={})
    agent = engine.active_agent
    agent.context_providers = [AsyncMock(return_value=None)]

    await engine._prepare_node(agent, three_node_workflow.nodes["end"])

    assert agent.context_providers == []


def test_knowledge_does_not_cross_an_agent_handoff():
    knowledge = IntegrationContextMessage(
        role="developer", content="1. Refunds take 5 days."
    )
    context = LLMContext(
        messages=[
            _user("Refunds?"),
            knowledge,
            {"role": "assistant", "content": "Five days."},
        ]
    )

    assert conversation_messages(context.messages) == [
        _user("Refunds?"),
        {"role": "assistant", "content": "Five days."},
    ]
    # Messages added while a handoff is prepared are deep copied before filtering.
    assert messages_after_boundary(context, 0) == conversation_messages(
        context.messages
    )
