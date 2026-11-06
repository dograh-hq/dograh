"""Record retained opening input at arrival, before it reaches generation."""

from dataclasses import replace

from pipecat.frames.frames import InputAudioRawFrame
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

_CAPTURE_ONLY = "dograh_opening_capture_only"
_CAPTURED = "dograh_opening_audio_captured"


class CallAudioBufferProcessor(AudioBufferProcessor):
    """Keep upstream recording behaviour, including output-clock alignment.

    Opening input can be held for seconds before the provider accepts it. Send
    a recording-only copy to this processor immediately; the eventual replay
    passes through without recording those samples a second time.
    """

    async def capture_opening_audio(self, frame: InputAudioRawFrame) -> None:
        if frame.metadata.get(_CAPTURED):
            return
        captured = replace(frame)
        captured.metadata = {**frame.metadata, _CAPTURE_ONLY: True}
        frame.metadata[_CAPTURED] = True
        await self.queue_frame(captured)

    async def process_frame(self, frame, direction):
        if isinstance(frame, InputAudioRawFrame) and frame.metadata.get(_CAPTURED):
            await FrameProcessor.process_frame(self, frame, direction)
            await self.push_frame(frame, direction)
            return
        await super().process_frame(frame, direction)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(frame, InputAudioRawFrame) and frame.metadata.get(_CAPTURE_ONLY):
            return
        await super().push_frame(frame, direction)
