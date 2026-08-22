"""Every in-pipeline termination routed through the engine, in order.

A call can end from inside the pipeline in two ways that do not go through
``PipecatEngine.end_call_with_reason``:

* the output transport gives up after ``audio_out_max_consecutive_failures``
  failed writes and pushes a ``CancelWorkerFrame`` upstream;
* a processor pushes a fatal ``ErrorFrame`` upstream, which the pipeline worker
  turns into a ``CancelFrame`` of its own.

Both reach the worker source, which cancels the pipeline and fires
``on_pipeline_finished`` -- the handler that snapshots the call's gathered
context, writes it and enqueues the completion job. Meanwhile the engine's
teardown is very often already running: a caller who hangs up triggers
``on_client_disconnected`` and the failed writes at the same moment. Whichever
finishes first wins, and measured over 238 hangups the pipeline won 24 times
(10.1%), taking the snapshot before the final extraction had landed and
shipping the placeholder disposition to the customer's dialer.

Terminating through the engine converts that race into an ordering. The only
thing that ends the pipeline is the terminal frame the engine queues on its
last line, so ``on_pipeline_finished`` cannot run early by construction -- the
same property that makes the end-node path exposed 0 times out of 86.

Placed immediately after ``transport.input()`` so that upstream frames from
every other processor pass through it first. Frames pushed upstream by the
input transport itself do not, and are still handled by the worker's own
``on_pipeline_error``.
"""

import asyncio
from typing import Awaitable, Callable, Optional

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    CancelWorkerFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StopFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.enums import EndTaskReason

# How long the engine gets to dispose of the call before the funnel stops
# waiting and lets the pipeline be cancelled the original way.
#
# Deferring termination means nothing else will end this pipeline, so a
# teardown that never returns would leak the worker, the telephony channel and
# every service the pipeline holds open. Before this processor existed the
# failed-write watchdog capped that at ~4.5s (10 writes x 0.5s) by accident;
# this is the same guarantee stated deliberately, and set far enough above the
# engine's own bounds that reaching it means something is genuinely stuck.
TERMINATION_GRACE_SECONDS = 20.0

# What a ``CancelWorkerFrame`` means when it carries no reason of its own, or
# one that is not a call outcome. The pipeline asked to stop and nothing about
# the conversation explains why.
_DEFAULT_CANCEL_REASON = EndTaskReason.SYSTEM_CANCELLED.value

_END_TASK_REASONS = frozenset(reason.value for reason in EndTaskReason)

TerminationHandler = Callable[[str, Optional[ErrorFrame]], Awaitable[None]]


class TerminationFunnelProcessor(FrameProcessor):
    """Intercept in-pipeline terminations and hand them to the engine.

    The handler is registered after construction because it closes over state
    -- the workflow run, the circuit breaker, the engine -- that is wired up
    once the pipeline exists. Until it is set, and after the funnel has already
    deferred one termination, frames pass straight through and the pipeline
    ends exactly as it did before.
    """

    def __init__(self) -> None:
        super().__init__()
        self._handler: Optional[TerminationHandler] = None
        self._funnelled = False
        self._pipeline_ending = asyncio.Event()

    def set_termination_handler(self, handler: TerminationHandler) -> None:
        """Register the coroutine that disposes of the call."""
        self._handler = handler

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # The engine's terminal frame, on its way down the pipeline. Releases
        # the grace timer: the shutdown the funnel was waiting for is happening.
        if isinstance(frame, (EndFrame, CancelFrame, StopFrame)):
            self._pipeline_ending.set()

        if direction == FrameDirection.UPSTREAM and self._should_funnel(frame):
            self._funnelled = True
            reason, error = self._classify(frame)
            logger.debug(f"Funnelling {frame} through the engine as '{reason}'")
            self.create_task(self._terminate(reason, error))
            return

        await self.push_frame(frame, direction)

    def _should_funnel(self, frame: Frame) -> bool:
        if self._handler is None or self._funnelled:
            # Nothing to hand the call to, or the engine already has it and is
            # taking too long: let the frame through and be cancelled the old
            # way rather than swallow the only remaining way out.
            return False
        if isinstance(frame, CancelWorkerFrame):
            return True
        return isinstance(frame, ErrorFrame) and frame.fatal

    def _classify(self, frame: Frame) -> tuple[str, Optional[ErrorFrame]]:
        if isinstance(frame, ErrorFrame):
            return EndTaskReason.PIPELINE_ERROR.value, frame
        reason = getattr(frame, "reason", None)
        if isinstance(reason, str) and reason in _END_TASK_REASONS:
            return reason, None
        return _DEFAULT_CANCEL_REASON, None

    async def _terminate(self, reason: str, error: Optional[ErrorFrame]) -> None:
        """Dispose of the call, and force the cancel through if that stalls."""
        assert self._handler is not None
        try:
            await asyncio.wait_for(
                self._handler(reason, error), timeout=TERMINATION_GRACE_SECONDS
            )
        except asyncio.TimeoutError:
            logger.error(
                f"Engine did not finish disposing of the call within "
                f"{TERMINATION_GRACE_SECONDS}s; cancelling the pipeline"
            )
        except Exception as exc:
            logger.error(f"Engine failed to dispose of the call: {exc}", exc_info=True)

        # The handler is disposed of, but a call already being torn down by
        # another path (a client disconnect, an end-call tool) returns from it
        # immediately and queues its terminal frame later. Wait for whichever
        # frame that is before deciding the shutdown never happened.
        if self._pipeline_ending.is_set():
            return
        try:
            await asyncio.wait_for(
                self._pipeline_ending.wait(), timeout=TERMINATION_GRACE_SECONDS
            )
            return
        except asyncio.TimeoutError:
            logger.error(
                f"No terminal frame {TERMINATION_GRACE_SECONDS}s after disposing "
                f"of the call; cancelling the pipeline"
            )

        await self.push_frame(CancelWorkerFrame(reason=reason), FrameDirection.UPSTREAM)
