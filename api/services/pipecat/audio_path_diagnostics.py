"""Low-volume diagnostics for the voice media and model path.

The processor is deliberately observational: it never changes frame routing.
Audio frames are counted and sampled so a live call can prove which boundary
was crossed without producing one log line for every 10 ms packet.
"""

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
    TTSStartedFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class AudioPathDiagnosticsProcessor(FrameProcessor):
    """Log microphone, STT, LLM, TTS, and outbound audio milestones."""

    def __init__(self, stage: str = "input"):
        super().__init__()
        self._stage = stage
        self._mic_frames = 0
        self._mic_nonzero_frames = 0
        self._tts_audio_frames = 0
        self._tts_audio_nonzero_frames = 0
        self._tts_audio_bytes = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            self._mic_frames += 1
            if any(frame.audio):
                self._mic_nonzero_frames += 1
            if self._mic_frames == 1 or self._mic_frames % 100 == 0:
                logger.info(
                    f"AUDIO_PATH microphone->Dograh [{self._stage}]: "
                    f"frames={self._mic_frames} nonzero={self._mic_nonzero_frames} "
                    f"bytes={len(frame.audio)} sample_rate={frame.sample_rate}"
                )
        elif isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            logger.info(
                f"AUDIO_PATH Dograh->STT transcript [{self._stage}]: "
                f"finalized={getattr(frame, 'finalized', None)} text_length={len(text)}"
            )
        elif isinstance(frame, LLMFullResponseStartFrame):
            logger.info(f"AUDIO_PATH STT->LLM response started [{self._stage}]")
        elif isinstance(frame, LLMTextFrame):
            text = (frame.text or "").strip()
            if text:
                logger.info(
                    f"AUDIO_PATH LLM response text [{self._stage}]: "
                    f"text_length={len(text)}"
                )
        elif isinstance(frame, LLMFullResponseEndFrame):
            logger.info(f"AUDIO_PATH LLM response ended [{self._stage}]")
        elif isinstance(frame, TTSStartedFrame):
            logger.info(f"AUDIO_PATH LLM->TTS synthesis started [{self._stage}]")
        elif isinstance(frame, TTSAudioRawFrame):
            self._tts_audio_frames += 1
            self._tts_audio_bytes += len(frame.audio)
            if any(frame.audio):
                self._tts_audio_nonzero_frames += 1
            if self._tts_audio_frames == 1 or self._tts_audio_frames % 25 == 0:
                logger.info(
                    f"AUDIO_PATH TTS audio produced->pipeline [{self._stage}]: "
                    f"frames={self._tts_audio_frames} nonzero={self._tts_audio_nonzero_frames} "
                    f"bytes={self._tts_audio_bytes} sample_rate={frame.sample_rate}"
                )
        elif isinstance(frame, TTSStoppedFrame):
            logger.info(
                f"AUDIO_PATH TTS synthesis ended [{self._stage}]: "
                f"audio_frames={self._tts_audio_frames} nonzero={self._tts_audio_nonzero_frames} "
                f"bytes={self._tts_audio_bytes}"
            )

        await self.push_frame(frame, direction)
