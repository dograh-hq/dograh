"""Tests for integration knowledge composed into the system instruction.

Covers apply_integration_context: the latest user message goes to every
provider, their answers follow the node prompt, a turn without knowledge
restores the node prompt, unchanged knowledge is not sent again, and a
provider that fails or misses its time budget is skipped. Also covers the
engine keeping the turn's knowledge when a transition changes the node.
"""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, Mock

import pytest

from api.services.pipecat import integration_context
from api.services.pipecat.integration_context import (
    apply_integration_context,
    last_user_text,
)
from api.services.workflow.pipecat_engine import PipecatEngine


def _agent(*providers):
    return types.SimpleNamespace(
        context_providers=list(providers),
        context_blocks=[],
        system_prompt="You are the support agent.",
        llm=types.SimpleNamespace(_update_settings=AsyncMock()),
    )


def _context(*messages):
    return types.SimpleNamespace(messages=list(messages))


def _sent_instruction(agent) -> str:
    return agent.llm._update_settings.await_args.args[0].system_instruction


def test_last_user_text_reads_the_newest_user_message():
    messages = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "an answer"},
        {"role": "user", "content": [{"type": "text", "text": " second question "}]},
        {"role": "assistant", "content": "thinking"},
    ]
    assert last_user_text(messages) == "second question"
    assert last_user_text([{"role": "assistant", "content": "hi"}]) == ""


async def test_knowledge_follows_the_node_prompt():
    provider = AsyncMock(return_value="1. Refunds take 5 days.")
    agent = _agent(provider)

    await apply_integration_context(
        agent, _context({"role": "user", "content": "How long do refunds take?"})
    )

    provider.assert_awaited_once_with("How long do refunds take?")
    assert _sent_instruction(agent) == (
        "You are the support agent.\n\n1. Refunds take 5 days."
    )


async def test_unchanged_knowledge_is_not_sent_again():
    agent = _agent(AsyncMock(return_value="1. Refunds take 5 days."))
    context = _context({"role": "user", "content": "Refunds?"})

    await apply_integration_context(agent, context)
    await apply_integration_context(agent, context)

    assert agent.llm._update_settings.await_count == 1


async def test_turn_without_knowledge_restores_the_node_prompt():
    provider = AsyncMock(side_effect=["1. Refunds take 5 days.", None])
    agent = _agent(provider)

    await apply_integration_context(agent, _context({"role": "user", "content": "a"}))
    await apply_integration_context(agent, _context({"role": "user", "content": "b"}))

    assert _sent_instruction(agent) == "You are the support agent."
    assert agent.context_blocks == []


async def test_agent_without_providers_is_left_alone():
    agent = _agent()
    await apply_integration_context(agent, _context({"role": "user", "content": "hi"}))
    agent.llm._update_settings.assert_not_awaited()


async def test_failing_or_slow_provider_is_skipped(monkeypatch):
    monkeypatch.setattr(integration_context, "PROVIDER_TIMEOUT_SECONDS", 0.01)

    async def slow(_text):
        await asyncio.sleep(1)
        return "too late"

    broken = AsyncMock(side_effect=RuntimeError("boom"))
    working = AsyncMock(return_value="1. Store credit never expires.")
    agent = _agent(slow, broken, working)

    await apply_integration_context(agent, _context({"role": "user", "content": "q"}))

    assert _sent_instruction(agent) == (
        "You are the support agent.\n\n1. Store credit never expires."
    )


@pytest.mark.parametrize("messages", [[], [{"role": "user", "content": "  "}]])
async def test_turn_without_user_text_asks_no_provider(messages):
    provider = AsyncMock(return_value="unused")
    agent = _agent(provider)

    await apply_integration_context(agent, _context(*messages))

    provider.assert_not_awaited()
    agent.llm._update_settings.assert_not_awaited()


async def test_transition_keeps_the_turn_knowledge(three_node_workflow):
    llm = Mock()
    llm._update_settings = AsyncMock()
    engine = PipecatEngine(workflow=three_node_workflow, llm=llm, call_context_vars={})
    agent = engine.active_agent
    agent.context_blocks = ["1. Refunds take 5 days."]

    await engine._prepare_node(agent, three_node_workflow.nodes["agent"])

    instruction = llm._update_settings.await_args.args[0].system_instruction
    assert instruction == agent.system_prompt + "\n\n1. Refunds take 5 days."


async def test_moving_to_the_end_node_keeps_the_turn_knowledge(three_node_workflow):
    llm = Mock()
    llm._update_settings = AsyncMock()
    engine = PipecatEngine(workflow=three_node_workflow, llm=llm, call_context_vars={})
    agent = engine.active_agent
    agent.context_blocks = ["1. Refunds take 5 days."]

    await engine._prepare_node(agent, three_node_workflow.nodes["end"])

    assert agent.context_providers == []
    instruction = llm._update_settings.await_args.args[0].system_instruction
    assert instruction.endswith("\n\n1. Refunds take 5 days.")
