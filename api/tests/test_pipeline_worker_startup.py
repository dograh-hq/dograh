"""Dograh worker setup cancellation stays compatible with unmodified Pipecat."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pipecat.bus import BusCancelWorkerMessage, BusEndWorkerMessage
from pipecat.frames.frames import CancelFrame, EndFrame, StartFrame, StopFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import ProcessorUnusablePolicy, WorkerParams
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.utils.asyncio.task_manager import TaskManager
from pipecat.workers.runner import WorkerRunner

from api.services.pipecat.agent_bridge import AgentWorker
from api.services.pipecat.pipeline_worker import DograhPipelineWorker


@pytest.fixture(params=["call", "agent"])
def make_worker(request):
    def create(processor, **kwargs):
        pipeline = Pipeline(processor if isinstance(processor, list) else [processor])
        kwargs.setdefault("enable_rtvi", False)
        if request.param == "agent":
            return AgentWorker(pipeline, call_worker_name="call", **kwargs)
        return DograhPipelineWorker(pipeline, **kwargs)

    return create


class ConnectingService(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleaned = asyncio.Event()
        self.cleanup_count = 0
        self.frames = []

    async def setup(self, setup):
        await super().setup(setup)
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def cleanup(self):
        self.cleanup_count += 1
        self.cleaned.set()
        await super().cleanup()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_path", ["cancel", "repeat_cancel", "external"])
async def test_cancel_during_setup_releases_partial_resources(make_worker, exit_path):
    service = ConnectingService()
    manager = TaskManager()
    worker = make_worker(service)
    running = asyncio.create_task(worker.run(WorkerParams(task_manager=manager)))
    try:
        await asyncio.wait_for(service.entered.wait(), 1)
        if exit_path == "external":
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 1)
        else:
            await worker.cancel(reason="caller hung up")
            if exit_path == "repeat_cancel":
                await worker.cancel(reason="duplicate hangup")
            await asyncio.wait_for(running, 1)
        assert worker.has_finished()
        assert service.cancelled.is_set()
        assert service.cleaned.is_set()
        assert service.cleanup_count == 1
        assert not any(isinstance(frame, StartFrame) for frame in service.frames)
        assert worker._dograh_setup_task is None
        assert not manager.current_tasks()
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_before_run_never_starts_processors(make_worker):
    service = ConnectingService()
    worker = make_worker(service)
    await worker.cancel()
    await asyncio.wait_for(worker.run(WorkerParams(task_manager=TaskManager())), 1)
    assert worker.has_finished()
    assert not service.entered.is_set()


@pytest.mark.asyncio
async def test_upstream_setup_timeout_still_reports_failure_and_cleans_up(make_worker):
    service = ConnectingService()
    manager = TaskManager()
    worker = make_worker(service, setup_timeout_secs=0.05)
    timed_out = AsyncMock()
    worker.add_event_handler("on_setup_timeout", timed_out)
    await asyncio.wait_for(worker.run(WorkerParams(task_manager=manager)), 1)
    timed_out.assert_awaited_once()
    assert worker.has_finished()
    assert service.cancelled.is_set()
    assert service.cleaned.is_set()
    assert not manager.current_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_path", ["cancel", "external", "end", "stop"])
async def test_shutdown_after_start_preserves_frame_and_cleanup_contract(
    make_worker, exit_path
):
    service = ConnectingService()
    service.release.set()
    manager = TaskManager()
    worker = make_worker(service)
    started = asyncio.Event()
    finished = AsyncMock()

    async def on_started(*_):
        started.set()

    worker.add_event_handler("on_pipeline_started", on_started)
    worker.add_event_handler("on_pipeline_finished", finished)
    running = asyncio.create_task(worker.run(WorkerParams(task_manager=manager)))
    try:
        await asyncio.wait_for(started.wait(), 1)
        if exit_path == "external":
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 1)
        else:
            if exit_path == "cancel":
                await worker.cancel(reason="hangup")
                await worker.cancel(reason="duplicate hangup")
            else:
                await worker.queue_frame(
                    EndFrame() if exit_path == "end" else StopFrame()
                )
            await asyncio.wait_for(running, 1)
        assert worker.has_finished()
        finished.assert_awaited_once()
        expected_frame = {
            "cancel": CancelFrame,
            "external": CancelFrame,
            "end": EndFrame,
            "stop": StopFrame,
        }[exit_path]
        assert sum(isinstance(frame, StartFrame) for frame in service.frames) == 1
        assert sum(isinstance(frame, expected_frame) for frame in service.frames) == 1
        # StopFrame deliberately leaves processors alive for their next owner.
        assert service.cleanup_count == (0 if exit_path == "stop" else 1)
        if exit_path == "stop":
            await worker.pipeline.cleanup()
        assert not manager.current_tasks()
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        if not service.cleaned.is_set():
            await worker.pipeline.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_path", ["cancel", "timeout"])
async def test_shutdown_while_start_frame_is_pending(make_worker, exit_path):
    class HeldStartService(ConnectingService):
        async def process_frame(self, frame, direction):
            await FrameProcessor.process_frame(self, frame, direction)
            if isinstance(frame, StartFrame):
                start_seen.set()
                return  # Simulate a provider which never finishes starting.
            await self.push_frame(frame, direction)

    start_seen = asyncio.Event()
    service = HeldStartService()
    service.release.set()
    manager = TaskManager()
    worker = make_worker(service, start_timeout_secs=0.05, cancel_timeout_secs=0.05)
    timed_out = AsyncMock()
    started = AsyncMock()
    worker.add_event_handler("on_pipeline_timeout", timed_out)
    worker.add_event_handler("on_pipeline_started", started)
    running = asyncio.create_task(worker.run(WorkerParams(task_manager=manager)))
    try:
        await asyncio.wait_for(start_seen.wait(), 1)
        if exit_path == "cancel":
            await worker.cancel()
        await asyncio.wait_for(running, 1)
        started.assert_not_awaited()
        if exit_path == "timeout":
            timed_out.assert_awaited_once()
            assert isinstance(timed_out.await_args.args[-1], StartFrame)
        assert service.cleanup_count == 1
        assert worker.has_finished()
        assert not manager.current_tasks()
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_service_cancels_other_services_still_setting_up(make_worker):
    blocked = ConnectingService()

    class FailedService(ConnectingService):
        async def setup(self, setup):
            await FrameProcessor.setup(self, setup)
            await blocked.entered.wait()
            raise RuntimeError("provider credentials rejected")

    failed = FailedService()
    manager = TaskManager()
    worker = make_worker(
        [failed, blocked], processor_unusable_policy=ProcessorUnusablePolicy.CANCEL
    )
    errors = AsyncMock()
    worker.add_event_handler("on_pipeline_error", errors)
    await asyncio.wait_for(worker.run(WorkerParams(task_manager=manager)), 1)
    assert any(
        "provider credentials rejected" in call.args[-1].error
        for call in errors.await_args_list
    )
    assert not failed.is_usable
    assert blocked.cancelled.is_set()
    assert failed.cleanup_count == blocked.cleanup_count == 1
    assert worker.has_finished()
    assert not manager.current_tasks()


@pytest.mark.asyncio
async def test_worker_setup_exception_releases_partial_resources(
    make_worker, monkeypatch
):
    service = ConnectingService()
    manager = TaskManager()
    worker = make_worker(service)
    monkeypatch.setattr(
        worker,
        "_load_setup_files",
        AsyncMock(side_effect=RuntimeError("setup hook failed")),
    )
    with pytest.raises(RuntimeError, match="setup hook failed"):
        await asyncio.wait_for(worker.run(WorkerParams(task_manager=manager)), 1)
    assert worker.has_finished()
    assert service.cleanup_count == 1
    assert not manager.current_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call_ready", [False, True], ids=["call-setting-up", "call-started"]
)
@pytest.mark.parametrize("exit_path", ["bus", "runner", "external", "end"])
async def test_runner_shutdown_reaches_inactive_child_during_setup(
    call_ready, exit_path
):
    """Exercise the real bus, parent propagation and runner task ownership."""
    manager = TaskManager()
    runner = WorkerRunner(
        task_manager=manager, handle_sigint=False, handle_sigterm=False, force_gc=False
    )
    call_service = ConnectingService()
    if call_ready:
        call_service.release.set()
    child_service = ConnectingService()
    call = DograhPipelineWorker(
        Pipeline([call_service]), name="call", enable_rtvi=False
    )
    child = AgentWorker(
        Pipeline([child_service]),
        name="agent",
        call_worker_name="call",
        enable_rtvi=False,
        active=False,
    )
    await runner.add_workers(call)
    running = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(call_service.entered.wait(), 1)
        await call.add_workers(child)
        await asyncio.wait_for(child_service.entered.wait(), 1)
        # Prepared agents have not activated yet, but must still receive cancel.
        assert not child.active
        if exit_path == "bus":
            await runner.bus.send(
                BusCancelWorkerMessage(source="test", target=call.name, reason="hangup")
            )
        elif exit_path == "runner":
            await runner.cancel(reason="hangup")
        elif exit_path == "external":
            running.cancel()
        else:
            # An end requested before readiness must finish once setup completes.
            await runner.bus.send(BusEndWorkerMessage(source="test", target=call.name))
            call_service.release.set()
            child_service.release.set()
        await asyncio.wait_for(asyncio.shield(running), 2)
        assert call.has_finished() and child.has_finished()
        assert call_service.cleanup_count == child_service.cleanup_count == 1
        if exit_path != "end":
            assert child_service.cancelled.is_set()
            assert not any(
                isinstance(frame, StartFrame) for frame in child_service.frames
            )
        assert not manager.current_tasks()
    finally:
        # Bound teardown even if a future upstream regression loses a bus cancel.
        call_service.release.set()
        child_service.release.set()
        await runner.cancel()
        await call.cancel()
        await child.cancel()
        _, pending = await asyncio.wait([running], timeout=2)
        if pending:
            tasks = [running, *manager.current_tasks()]
            for task in tasks:
                task.cancel()
            await asyncio.wait(tasks, timeout=1)


@pytest.mark.asyncio
async def test_successful_setup_preserves_normal_start_and_shutdown(make_worker):
    service = ConnectingService()
    service.release.set()
    manager = TaskManager()
    worker = make_worker(service, enable_rtvi=False)
    started = AsyncMock()
    worker.add_event_handler("on_pipeline_started", started)
    await worker.queue_frame(EndFrame())
    await asyncio.wait_for(worker.run(WorkerParams(task_manager=manager)), 2)
    started.assert_awaited_once()
    assert service.entered.is_set()
    assert service.cleaned.is_set()
    assert not service.cancelled.is_set()
    assert not manager.current_tasks()
