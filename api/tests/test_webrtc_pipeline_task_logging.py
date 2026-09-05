"""The smallwebrtc pipeline task must never leak an unretrieved exception."""

import asyncio

import loguru
from fastapi import HTTPException

from api.errors.failure import mark_failure_reported
from api.routes.webrtc_signaling import SignalingManager


async def _finished_task(exc: BaseException | None = None) -> asyncio.Task:
    async def run():
        if exc is not None:
            raise exc

    task = asyncio.create_task(run())
    await asyncio.gather(task, return_exceptions=True)
    return task


def _drain_task(manager: SignalingManager, task: asyncio.Task) -> list:
    records = []
    handler_id = loguru.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    try:
        manager._on_pipeline_task_done(task, 123)
    finally:
        loguru.logger.remove(handler_id)
    return records


async def test_rejected_pipeline_start_logs_info():
    manager = SignalingManager()
    task = await _finished_task(
        HTTPException(status_code=400, detail="Workflow run already completed")
    )
    manager._pipeline_tasks.add(task)

    records = _drain_task(manager, task)

    assert task not in manager._pipeline_tasks
    assert [record["level"].name for record in records] == ["INFO"]
    assert "Workflow run already completed" in records[0]["message"]


async def test_unclaimed_pipeline_failure_is_reported_classified():
    """The sole report of a crash nobody claimed must carry source and run id.

    Left bare it reaches the operator channel as an anonymous
    ``unclassified-error`` with no run to trace it back to.
    """
    manager = SignalingManager()
    task = await _finished_task(ValueError("boom"))

    records = _drain_task(manager, task)

    assert [record["level"].name for record in records] == ["ERROR"]
    extra = records[0]["extra"]
    assert extra["error_source"] == "platform"
    assert extra["error_code"] != "unclassified-error"
    assert extra["exception_type"] == "ValueError"
    assert extra["workflow_run_id"] == 123


async def test_reported_pipeline_failure_logs_warning():
    manager = SignalingManager()
    exc = ValueError("boom")
    mark_failure_reported(exc)
    task = await _finished_task(exc)

    records = _drain_task(manager, task)

    assert [record["level"].name for record in records] == ["WARNING"]


async def test_cancelled_pipeline_task_is_silent():
    manager = SignalingManager()

    async def run():
        await asyncio.sleep(30)

    task = asyncio.create_task(run())
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert _drain_task(manager, task) == []


async def test_successful_pipeline_task_is_silent():
    manager = SignalingManager()
    task = await _finished_task()

    assert _drain_task(manager, task) == []
