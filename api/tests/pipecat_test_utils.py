"""Shared helpers for running Pipecat engine tests with production-like startup."""

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from pipecat.frames.frames import LLMContextFrame
from pipecat.pipeline.worker import PipelineWorker

from api.services.pipecat.worker_runner import run_pipeline_worker
from api.services.workflow.agent_runtime import AgentRuntime
from api.services.workflow.pipecat_engine import PipecatEngine

ReadyCallback = Callable[[], Awaitable[None]]


async def run_engine_test_pipeline(
    task: PipelineWorker,
    engine: PipecatEngine,
    transport: Any,
    *,
    on_ready: ReadyCallback | None = None,
    timeout: float | None = 10.0,
) -> None:
    """Run an engine pipeline after the same readiness signals used in production.

    Production initializes the engine before starting the worker, then starts the
    conversation only after both the transport client and pipeline are ready. Tests
    use a direct ``LLMContextFrame`` as their default stimulus because they are
    exercising an LLM response rather than the configured node greeting.
    """
    await engine.initialize()

    ready_state = {
        "pipeline_started": False,
        "client_connected": False,
        "callback_started": False,
    }

    async def trigger_test() -> None:
        if on_ready is not None:
            await on_ready()
            return

        await engine.set_node(engine.active_agent.workflow.start_node_id)
        await engine.active_agent.llm.queue_frame(LLMContextFrame(engine.context))

    async def maybe_trigger_test() -> None:
        if (
            ready_state["pipeline_started"]
            and ready_state["client_connected"]
            and not ready_state["callback_started"]
        ):
            ready_state["callback_started"] = True
            await trigger_test()

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _participant) -> None:
        ready_state["client_connected"] = True
        await maybe_trigger_test()

    @task.event_handler("on_pipeline_started")
    async def on_pipeline_started(_task, _frame) -> None:
        ready_state["pipeline_started"] = True
        await maybe_trigger_test()

    if timeout is None:
        await run_pipeline_worker(task)
    else:
        async with asyncio.timeout(timeout):
            await run_pipeline_worker(task)


def stub_agent_runtime(
    *,
    queue_frame: Callable[[Any], Awaitable[None]] | None = None,
    llm: Any = None,
    tts: Any = None,
    visit_id: str = "visit-test",
) -> AgentRuntime:
    """An ``AgentRuntime`` for stubs that borrow real ``PipecatEngine`` methods.

    Speech and generation go through the running agent, so a stub that only
    sets ``task`` never sees the frames it is asserting on. Assign the result
    to the stub's ``_active_agent``.

    Args:
        queue_frame: Where frames the agent speaks are delivered. Usually the
            test's own recorder.
        llm: Stands in for the agent's conversation LLM.
        tts: Stands in for the agent's TTS service.
        visit_id: Visit identifier, for tests that assert attribution.
    """
    worker = SimpleNamespace(queue_frame=queue_frame or AsyncMock())
    return AgentRuntime(
        visit_id=visit_id,
        workflow_id=0,
        definition_id=None,
        workflow_name="test",
        workflow=None,
        llm=llm,
        inference_llm=llm,
        variable_extraction_llm=llm,
        worker=worker,
        tts=tts,
    )
