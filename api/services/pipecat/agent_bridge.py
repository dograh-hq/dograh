"""The call's routing boundary for replaceable agent workers."""

from collections.abc import Callable

from pipecat.bus.bridge_processor import BusBridgeProcessor
from pipecat.bus.messages import BusFrameMessage, BusMessage
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    CancelWorkerFrame,
    EndFrame,
    EndWorkerFrame,
    ErrorFrame,
    Frame,
    HeartbeatFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    InterruptionWorkerFrame,
    LLMContextFrame,
    MetricsFrame,
    OutputTransportMessageUrgentFrame,
    PipelineFlushFrame,
    StartFrame,
    StopFrame,
    StopWorkerFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# These must reach both the call processors and the selected agent exactly once.
TEED_FRAMES = (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
CALL_FRAMES = (
    StartFrame,
    EndFrame,
    CancelFrame,
    StopFrame,
    InputAudioRawFrame,
    HeartbeatFrame,
    CancelWorkerFrame,
    EndWorkerFrame,
    StopWorkerFrame,
    InterruptionWorkerFrame,
    MetricsFrame,
    ErrorFrame,
    OutputTransportMessageUrgentFrame,
)
AGENT_EDGE_EXCLUDED_FRAMES = TEED_FRAMES + (ErrorFrame,)


class AgentBridgeProcessor(BusBridgeProcessor):
    """Route speech to/from one visit; keep recording and call control local.

    An upstream worker's ``active`` flag gates its input only. This boundary
    also rejects late output from a source or candidate. Usage from every
    visit still reaches the call's metrics collector.
    """

    def __init__(
        self,
        *,
        selected_visit: Callable[[], str | None],
        allow_inference: Callable[[], bool],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._selected_visit = selected_visit
        self._allow_inference = allow_inference

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await FrameProcessor.process_frame(self, frame, direction)
        if isinstance(frame, CALL_FRAMES + TEED_FRAMES):
            await self.push_frame(frame, direction)
            if not isinstance(frame, TEED_FRAMES):
                return
        target = self._selected_visit()
        if isinstance(frame, LLMContextFrame) and not self._allow_inference():
            return
        if isinstance(frame, PipelineFlushFrame) and target is None:
            await self.push_frame(frame, direction)
            return
        if target is not None:
            await self._bus.send(
                BusFrameMessage(
                    source=self._worker_name,
                    target=target,
                    frame=frame,
                    direction=direction,
                )
            )

    async def on_bus_message(self, message: BusMessage) -> None:
        if not isinstance(message, BusFrameMessage):
            return
        if message.source != self._selected_visit() and not isinstance(
            message.frame, MetricsFrame
        ):
            return
        await super().on_bus_message(message)


class AgentWorker(PipelineWorker):
    """A child exchanges frames with its call, never with another child."""

    def __init__(self, *args, call_worker_name: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._call_worker_name = call_worker_name

    async def _queue_bridged_frame(self, message: BusFrameMessage) -> None:
        if message.source == self._call_worker_name:
            await super()._queue_bridged_frame(message)
