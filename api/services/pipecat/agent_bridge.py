"""Frame routing between the persistent call pipeline and per-agent workers.

The call pipeline keeps everything that must survive an agent transfer --
transport, recording, recognition, the shared context aggregators, the call
timer -- and hands generation to a child :class:`PipelineWorker` per agent
visit. Frames move between the two over the worker bus.

``BusBridgeProcessor`` *consumes*: a frame it publishes does not continue down
the local pipeline (``pipecat/bus/bridge_processor.py``). Everything after the
bridge in the call pipeline -- the output transport, the audio buffer, the
assistant aggregator, the metrics aggregator -- therefore sees only what a
child sends back. Two frame groups break under that rule:

``_LOCAL_FRAMES``
    Never useful to a child, and needed locally. Caller audio is the whole
    group: recognition is persistent, so no child ever wants raw caller PCM,
    while the audio buffer downstream of the bridge needs every frame of it or
    the caller's side of the recording is lost for the entire call, not just
    during a handoff.

``TEED_FRAMES``
    Needed on *both* sides. Speaking and interruption frames drive the call
    pipeline's mute strategies, playback tracking and recording segmentation,
    and at the same time drive the child's TTS pause/resume and generation
    cancellation. They also originate on both sides of the bridge: the output
    transport emits bot-speaking frames upstream, the user aggregator emits
    interruptions downstream.

A teed frame is published by :class:`AgentBusTeeProcessor` (which sits just
before the bridge and always pushes locally) and excluded from the bridge, so
it crosses the bus exactly once while still reaching every local processor. The
child excludes the same set from its own edges, so a teed frame is never
echoed back and delivered twice.
"""

from loguru import logger

from pipecat.bus.bus import WorkerBus
from pipecat.bus.messages import BusFrameMessage
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
    StartFrame,
    StopFrame,
    StopWorkerFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Frames both the call pipeline and the active agent act on. Published by the
# tee, excluded from the bridge and from the child's edges.
TEED_FRAMES: tuple[type[Frame], ...] = (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)

# Frames that stay in the call pipeline and never cross the bus.
#
# Caller audio: recognition is persistent, so no child ever wants raw caller
# PCM, and the audio buffer downstream of the bridge needs every frame of it.
#
# The worker's own liveness probe: a `HeartbeatFrame` is queued by the call
# worker and has to reach the call worker's sink. Handed to the bus instead it
# only comes back when a child happens to be active, so the worker reports
# "heartbeat frame not received" during every hold.
#
# Worker-control frames: each addresses the worker whose pipeline it is
# travelling in. The output transport pushes `CancelWorkerFrame` upstream when
# audio writes keep failing, and the call's termination funnel sits upstream of
# the bridge to catch it -- across the bus it would never arrive, and a call
# with a dead audio path would never end.
_LOCAL_FRAMES: tuple[type[Frame], ...] = (
    InputAudioRawFrame,
    HeartbeatFrame,
    CancelWorkerFrame,
    EndWorkerFrame,
    StopWorkerFrame,
    InterruptionWorkerFrame,
)

# Lifecycle frames are each worker's own; the bus never carries them.
_LIFECYCLE_FRAMES: tuple[type[Frame], ...] = (
    StartFrame,
    EndFrame,
    CancelFrame,
    StopFrame,
)

# What the call-side bridge keeps local.
BRIDGE_EXCLUDED_FRAMES: tuple[type[Frame], ...] = _LOCAL_FRAMES + TEED_FRAMES

# What an agent worker's edges keep local.
#
# Teed frames, so the copy the tee published is not echoed back and delivered
# twice. And errors: the call pipeline's termination funnel treats a terminal
# ErrorFrame as the end of the call, which is right for the agent talking to
# the caller and wrong for one still being prepared under a hold ringer. The
# engine decides instead, from the agent worker's own ``on_pipeline_error``,
# where it knows whose error it is.
AGENT_EDGE_EXCLUDED_FRAMES: tuple[type[Frame], ...] = TEED_FRAMES + (ErrorFrame,)


class AgentBusTeeProcessor(FrameProcessor):
    """Publish :data:`TEED_FRAMES` to the bus while passing everything through.

    Placed immediately before the call pipeline's bridge. Every frame continues
    down (or up) the local pipeline unchanged; the teed types are additionally
    sent to the bus so the active agent worker sees them too.

    Unlike ``_BusEdgeProcessor``, which publishes only one direction, this tees
    both: bot-speaking frames reach it travelling upstream from the output
    transport, interruptions travelling downstream from the user aggregator,
    and the agent needs both.
    """

    def __init__(
        self,
        *,
        bus: WorkerBus,
        worker_name: str,
        tee_frames: tuple[type[Frame], ...] = TEED_FRAMES,
        **kwargs,
    ):
        """Initialize the tee.

        Args:
            bus: The worker bus shared with the agent workers.
            worker_name: Name of the owning worker, used as the message source.
            tee_frames: Frame types to publish in addition to pushing locally.
            **kwargs: Additional arguments passed to ``FrameProcessor``.
        """
        super().__init__(**kwargs)
        self._bus = bus
        self._worker_name = worker_name
        self._tee_frames = tee_frames

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Push the frame on locally, and publish it when it is teed."""
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        if isinstance(frame, _LIFECYCLE_FRAMES):
            return
        if not isinstance(frame, self._tee_frames):
            return

        await self._bus.send(
            BusFrameMessage(
                source=self._worker_name,
                frame=frame,
                direction=direction,
            )
        )


async def assert_caller_audio_stays_local(
    bridge_excluded: tuple[type[Frame], ...] = BRIDGE_EXCLUDED_FRAMES,
) -> None:
    """Fail loudly if caller audio would be handed to the bus.

    The symptom of getting this wrong is a silent one -- calls sound normal and
    the recording is missing the caller -- so the invariant is asserted at
    startup rather than discovered from a support ticket.
    """
    missing = [f.__name__ for f in _LOCAL_FRAMES if f not in bridge_excluded]
    if missing:
        raise RuntimeError(
            "Agent bridge would publish caller audio to the bus, breaking call "
            f"recording: {', '.join(missing)} missing from the exclusion list"
        )
    logger.debug("Agent bridge exclusion list keeps caller audio local")
