import asyncio
from types import SimpleNamespace

import pytest

from api.services.workflow.pipecat_engine_context_summarizer import (
    ContextSummarizationManager,
)


@pytest.mark.asyncio
async def test_restarting_and_cleanup_await_cancelled_summarization_tasks():
    manager = ContextSummarizationManager(
        SimpleNamespace(_current_node=SimpleNamespace(name="test-node"))
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
    manager = ContextSummarizationManager(SimpleNamespace(_current_node=None))

    await manager.start()

    assert manager._summarization_task is None
