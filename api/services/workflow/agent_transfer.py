"""One engine-owned handoff task; prepare privately, then commit synchronously."""

from __future__ import annotations

import asyncio
import time
import uuid
from builtins import BaseExceptionGroup
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger
from pipecat.frames.frames import LLMMessagesAppendFrame

from api.services.pipecat.agent_runtime_factory import AgentBuildError
from api.services.pipecat.audio_playback import play_hold_audio_loop
from api.services.workflow.agent_handoff_context import build_handoff_snapshot
from api.services.workflow.agent_runtime import new_visit_id

TRANSFER_PREPARE_TIMEOUT_SECONDS = 20.0
TRANSFER_ACTIVATION_TIMEOUT_SECONDS = 5.0
TRANSFER_MIN_HOLD_SECONDS = 1.2


class TransferPhase(str, Enum):
    IDLE = "idle"
    ANNOUNCING = "announcing"
    PREPARING = "preparing"
    COMMITTING = "committing"
    OPENING = "opening"


@dataclass
class TransferRequest:
    destination_workflow_id: int
    destination_label: str
    origin_visit_id: str
    announcement: str | None = None
    request_id: str = field(default_factory=lambda: f"xfer-{uuid.uuid4().hex[:10]}")
    cancelled_reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self.cancelled_reason is not None


class AgentTransferCoordinator:
    """Sequence handoffs beneath the call engine, one request at a time."""

    def __init__(self, engine):
        self._engine = engine
        self._phase = TransferPhase.IDLE
        self._request: TransferRequest | None = None
        self._task: asyncio.Task | None = None
        self._hold_stop: asyncio.Event | None = None
        self._hold_task: asyncio.Task | None = None

    @property
    def phase(self):
        return self._phase

    @property
    def in_progress(self):
        return self._request is not None

    @property
    def completed(self):
        return list(self._engine._transfer_outcomes)

    def accept(self, request):
        if (
            self.in_progress
            or self._engine.is_call_disposed()
            or request.origin_visit_id != self._engine.active_agent.visit_id
        ):
            return False
        self._request = request
        self._phase = TransferPhase.ANNOUNCING
        return True

    def start(self, request, *, context_ready=None):
        if self._request is not request or request.cancelled:
            return
        self._task = asyncio.create_task(
            self._run(request, context_ready=context_ready),
            name=f"transfer:{request.request_id}",
        )
        self._task.add_done_callback(self._report_error)

    @staticmethod
    def _report_error(task):
        if not task.cancelled() and task.exception() is not None:
            logger.opt(exception=task.exception()).error(
                "Agent handoff failed during cleanup"
            )

    async def invalidate(self, reason):
        if self._request is not None:
            self._request.cancelled_reason = reason
        task = self._task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._stop_hold_audio()
        self._request = None
        self._phase = TransferPhase.IDLE

    def _check_current(self, request):
        if request.cancelled or self._engine.is_call_disposed():
            raise asyncio.CancelledError
        if self._request is not request:
            raise AgentBuildError("stale_request", "The handoff is no longer current")

    async def _run(self, request, *, context_ready=None):
        engine = self._engine
        source = engine.active_agent
        destination = None
        committed = False
        outcome = "completed"
        try:
            if source.visit_id != request.origin_visit_id:
                raise AgentBuildError("stale_origin", "The requesting visit has ended")
            if context_ready is not None:
                await asyncio.wait_for(context_ready.wait(), timeout=5)
            await self._announce(request, source)
            self._check_current(request)
            await self._begin_hold(request, source)
            hold_started = time.monotonic()

            async def prepare():
                nonlocal destination
                destination = await engine.build_agent(
                    workflow_id=request.destination_workflow_id, visit_id=new_visit_id()
                )
                if not await destination.wait_until_started():
                    raise AgentBuildError(
                        "destination_not_ready", "Destination did not start"
                    )
                await engine.prepare_agent(destination)
                # Ready and activated while the call boundary still blocks all
                # candidate input/output. No shared prompt or tools change yet.
                if not await engine.activate_agent(
                    destination, timeout=TRANSFER_ACTIVATION_TIMEOUT_SECONDS
                ):
                    raise AgentBuildError(
                        "activation_failed", "Destination did not activate"
                    )
                if destination.error:
                    raise AgentBuildError("destination_unusable", destination.error)

            async with asyncio.timeout(TRANSFER_PREPARE_TIMEOUT_SECONDS):
                self._phase = TransferPhase.PREPARING
                await engine.pause_background_context_writers()
                async with asyncio.TaskGroup() as group:
                    group.create_task(prepare())
                    compact = group.create_task(
                        build_handoff_snapshot(
                            engine.context,
                            source.inference_llm,
                            request_id=request.request_id,
                        )
                    )
                snapshot = compact.result()

            await asyncio.sleep(
                max(0, TRANSFER_MIN_HOLD_SECONDS - (time.monotonic() - hold_started))
            )
            await self._stop_hold_audio()
            await engine.drain_call_pipeline()
            self._check_current(request)
            if destination.error:
                raise AgentBuildError("destination_unusable", destination.error)
            self._phase = TransferPhase.COMMITTING
            engine.commit_agent(destination, snapshot)
            committed = True
            self._phase = TransferPhase.OPENING
            await engine.notify_agent_entered(destination)
            await engine.queue_node_opening(
                node_id=destination.workflow.start_node_id,
                generate_if_no_greeting=True,
                origin_visit_id=destination.visit_id,
            )
        except asyncio.CancelledError:
            outcome = request.cancelled_reason or "cancelled"
            raise
        except Exception as error:
            while isinstance(error, BaseExceptionGroup):
                error = error.exceptions[0]
            outcome = (
                error.reason
                if isinstance(error, AgentBuildError)
                else (
                    "prepare_timeout"
                    if isinstance(error, TimeoutError)
                    else "opening_failed"
                    if committed
                    else "internal_error"
                )
            )
            logger.opt(exception=error).warning(
                f"[transfer] {request.request_id}: {outcome}"
            )
        finally:
            try:
                await self._stop_hold_audio()
                if committed:
                    await source.retire("transferred")
                elif destination is not None:
                    try:
                        await destination.abort("transfer abandoned")
                    finally:
                        engine.discard_pending_agent(destination)
                # The source and its shared conversation were never replaced.
                if not committed and not engine.is_call_disposed():
                    if await engine.activate_agent(
                        source, timeout=TRANSFER_ACTIVATION_TIMEOUT_SECONDS
                    ):
                        engine._agent_on_hold = False
            finally:
                self._finish(
                    request, outcome, source, destination if committed else None
                )

        if not committed and engine.agent_can_act(source):
            await source.queue_frame(
                LLMMessagesAppendFrame(
                    [
                        {
                            "role": "user",
                            "content": (
                                f"System note: the transfer to {request.destination_label} could not be completed. "
                                "You are still speaking with the caller. Apologize briefly for the wait "
                                "and continue helping them yourself."
                            ),
                        }
                    ],
                    run_llm=True,
                )
            )

    async def _announce(self, request, source):
        engine = self._engine
        if request.announcement:
            engine.arm_speech_playback()
            spoken = await engine.queue_text_message(
                request.announcement, append_to_context=True, mute_user=True
            )
            if not spoken or not await engine.wait_for_speech_playback():
                engine.clear_queued_speech_mute()
        # Also drain when the tool has no announcement.
        await source.cancel_tools()
        await engine.drain_call_pipeline()

    async def _begin_hold(self, request, source):
        await self._engine.deactivate_agent(source)
        self._hold_stop = asyncio.Event()
        self._hold_task = asyncio.create_task(
            play_hold_audio_loop(
                stop_event=self._hold_stop,
                sample_rate=self._engine.hold_audio_sample_rate,
                queue_frame=self._engine.transport_output_queue_frame,
            ),
            name=f"hold:{request.request_id}",
        )

    async def _stop_hold_audio(self):
        if self._hold_stop is not None:
            self._hold_stop.set()
        task = self._hold_task
        self._hold_task = None
        self._hold_stop = None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=2)
            except TimeoutError:
                logger.warning("Hold audio producer timed out while stopping")

    def _finish(self, request, outcome, source, destination):
        self._phase = TransferPhase.IDLE
        self._request = None
        self._engine.record_transfer_outcome(
            {
                "request_id": request.request_id,
                "outcome": outcome,
                "destination_workflow_id": request.destination_workflow_id,
                "destination_label": request.destination_label,
                "from_visit_id": source.visit_id,
                "to_visit_id": destination.visit_id if destination else None,
            }
        )
        logger.info(f"[transfer] {request.request_id} {outcome}")
