"""Shared helpers for running Pipecat engine tests with production-like startup."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pipecat.frames.frames import LLMContextFrame
from pipecat.pipeline.worker import PipelineWorker

from api.services.pipecat.worker_runner import run_pipeline_worker
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

        await engine.set_node(engine.workflow.start_node_id)
        await engine.llm.queue_frame(LLMContextFrame(engine.context))

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
