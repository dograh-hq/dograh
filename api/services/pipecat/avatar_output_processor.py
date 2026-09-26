"""Pipeline tap that drives the SpatialReal avatar in host mode.

Sits immediately after ``transport.output()`` (both classic and realtime
pipelines), where bot speech is available as ``OutputAudioRawFrame``s at the
pipeline sample rate (16 kHz mono PCM16 — exactly the avatar session's input
format), regardless of whether it came from a TTS service or a
speech-to-speech realtime LLM.

Forwards every frame unmodified; the avatar work happens off the frame path
via fire-and-forget tasks so avatar backend latency can never stall audio to
the caller.
"""

import asyncio

from loguru import logger

from api.services.avatar import AvatarRunSession
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class AvatarOutputProcessor(FrameProcessor):
    """Streams bot output audio into the run's avatar session.

    Args:
        avatar_session: The run's :class:`AvatarRunSession`. The processor is
            only inserted when host mode is enabled and the session exists.
    """

    def __init__(self, *, avatar_session: AvatarRunSession, **kwargs):
        super().__init__(**kwargs)
        self._avatar = avatar_session
        self._sent_audio_this_turn = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, OutputAudioRawFrame):
            self._sent_audio_this_turn = True
            self._dispatch(self._avatar.send_audio(frame.audio, end=False))
        elif isinstance(frame, BotStoppedSpeakingFrame):
            if self._sent_audio_this_turn:
                self._sent_audio_this_turn = False
                self._dispatch(self._avatar.send_audio(b"", end=True))
        elif isinstance(frame, InterruptionFrame):
            self._sent_audio_this_turn = False
            self._dispatch(self._avatar.interrupt())

        await self.push_frame(frame, direction)

    def _dispatch(self, coro) -> None:
        task = asyncio.ensure_future(coro)

        def _log_failure(t: asyncio.Task) -> None:
            if not t.cancelled() and t.exception() is not None:
                logger.warning(f"Avatar dispatch failed: {t.exception()}")

        task.add_done_callback(_log_failure)
