"""Shared helper for pushing pre-recorded audio frames into a pipeline."""

import uuid
from typing import Awaitable, Callable

from pipecat.frames.frames import (
    Frame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)


async def queue_recording_audio(
    audio_data: bytes,
    *,
    sample_rate: int,
    queue_frame: Callable[[Frame], Awaitable[None]],
) -> None:
    """Push TTSStarted → TTSAudioRaw → TTSStopped frames.

    This is the canonical way to play pre-recorded PCM audio through the
    pipeline outside of the RecordingRouterProcessor (which uses its own
    ``push_frame`` path).

    Args:
        audio_data: Raw 16-bit mono PCM bytes.
        sample_rate: Pipeline sample rate (e.g. 16000).
        queue_frame: Typically ``task.queue_frame``.
    """
    context_id = str(uuid.uuid4())
    await queue_frame(TTSStartedFrame(context_id=context_id))
    await queue_frame(
        TTSAudioRawFrame(
            audio=audio_data,
            sample_rate=sample_rate,
            num_channels=1,
            context_id=context_id,
        )
    )
    await queue_frame(TTSStoppedFrame(context_id=context_id))
