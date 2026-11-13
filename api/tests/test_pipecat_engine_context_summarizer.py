import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.pipecat.integration_context import IntegrationContextMessage
from api.services.workflow.pipecat_engine_context_summarizer import (
    ContextSummarizationManager,
)


@pytest.mark.asyncio
async def test_restarting_and_cleanup_await_cancelled_summarization_tasks():
    manager = ContextSummarizationManager(
        SimpleNamespace(
            active_agent=SimpleNamespace(current_node=SimpleNamespace(name="test-node"))
        )
    )
    stopped = []

    async def wait_forever():
        task = asyncio.current_task()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(task)

    manager._summarize_context_in_background = wait_forever

    await manager.start()
    first_task = manager._summarization_task
    await asyncio.sleep(0)

    await manager.start()
    second_task = manager._summarization_task
    await asyncio.sleep(0)

    assert first_task is not None and first_task.done()
    assert first_task in stopped
    assert second_task is not None and not second_task.done()

    await manager.cleanup()

    assert second_task.done()
    assert second_task in stopped
    assert manager._summarization_task is None


@pytest.mark.asyncio
async def test_start_without_current_node_does_not_create_task():
    manager = ContextSummarizationManager(
        SimpleNamespace(active_agent=SimpleNamespace(current_node=None))
    )

    await manager.start()

    assert manager._summarization_task is None


@pytest.mark.asyncio
async def test_integration_knowledge_stays_out_of_the_summary(monkeypatch):
    monkeypatch.setattr(
        "api.services.workflow.pipecat_engine_context_summarizer.ensure_tracing",
        lambda: False,
    )
    knowledge = IntegrationContextMessage(
        role="developer", content="1. Refunds take 5 days."
    )
    turns = [
        {"role": "user", "content": f"question {n}"}
        if n % 2 == 0
        else {"role": "assistant", "content": f"answer {n}"}
        for n in range(6)
    ]
    tail = [{"role": "user", "content": "refunds?"}, knowledge]
    tail += [
        {"role": "assistant", "content": "moving on"},
        {"role": "tool", "content": "ok"},
    ]
    context = LLMContext(messages=turns + tail)
    summarized_inputs = []

    async def generate_summary(request_frame):
        summarized_inputs.extend(request_frame.context.messages)
        return "summary text", len(request_frame.context.messages) - 3

    llm = SimpleNamespace(_generate_summary=AsyncMock(side_effect=generate_summary))
    engine = SimpleNamespace(
        context=context,
        active_agent=SimpleNamespace(
            inference_llm=llm, current_node=SimpleNamespace(id="agent", name="Agent")
        ),
        _get_otel_context=lambda: None,
    )

    await ContextSummarizationManager(engine)._summarize_context_in_background()

    assert knowledge not in summarized_inputs
    assert any(m is knowledge for m in context.messages)
    assert context.messages[-2:] == tail[-2:]
