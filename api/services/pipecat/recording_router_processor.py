"""Recording router processor for routing LLM output between TTS and pre-recorded audio.

Sits between the LLM (after pipeline_engine_callbacks_processor) and TTS in the
pipeline. Detects response mode markers (▸ for TTS, ● for recording) and routes
accordingly:

- ▸ (TTS): Strips the marker, passes remaining text downstream to TTS.
- ● (Recording): Suppresses TTS, fetches cached audio, pushes
  OutputAudioRawFrame downstream.

Pattern modelled after ``pipecat.turns.user_turn_completion_mixin`` – buffer
streaming LLM text tokens until the mode marker is detected, then act.
"""

import uuid
from typing import Awaitable, Callable, Optional

from loguru import logger

from api.services.pipecat.recording_audio_cache import RecordingAudio
from api.services.workflow.pipecat_engine_context_composer import (
    RECORDING_MARKER,
    TTS_MARKER,
)
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class RecordingRouterProcessor(FrameProcessor):
    """Routes LLM responses between TTS and pre-recorded audio playback.

    When the LLM prefixes its response with:
    - ``▸`` – text flows to TTS as normal speech.
    - ``●`` – text is suppressed (skip_tts), and the referenced recording is
      fetched (with local disk cache) and streamed as ``OutputAudioRawFrame``.

    If no marker is detected by the end of the response, text is passed through
    to TTS as a graceful degradation.

    Args:
        audio_sample_rate: Pipeline sample rate for OutputAudioRawFrame.
        fetch_recording_audio: Async callback that takes a recording_id and
            returns a RecordingAudio (audio + transcript), or None on failure.
    """

    def __init__(
        self,
        *,
        audio_sample_rate: int,
        fetch_recording_audio: Callable[..., Awaitable[Optional[RecordingAudio]]],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._audio_sample_rate = audio_sample_rate
        self._fetch_recording_audio = fetch_recording_audio

        # Per-response state
        self._frame_buffer: list[tuple[LLMTextFrame, FrameDirection]] = []
        self._mode: Optional[str] = None  # None = detecting, "tts", "recording"
        self._recording_id_buffer = ""
        self._recording_playback_started = False
        self._second_marker_seen = False

    # ------------------------------------------------------------------
    # Frame dispatch
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            self._reset()
            await self.push_frame(frame, direction)
        elif isinstance(frame, LLMTextFrame):
            await self._handle_llm_text(frame, direction)
        elif isinstance(frame, LLMFullResponseEndFrame):
            await self._handle_response_end(frame, direction)
        else:
            await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    # LLMTextFrame handling
    # ------------------------------------------------------------------

    async def _handle_llm_text(self, frame: LLMTextFrame, direction: FrameDirection):
        # Pass through frames already marked skip_tts (e.g. turn completion ✓)
        if frame.skip_tts:
            await self.push_frame(frame, direction)
            return

        # --- Second marker already seen — drop everything ---
        if self._second_marker_seen:
            return

        # --- TTS mode established: pass text through normally ---
        if self._mode == "tts":
            if RECORDING_MARKER in frame.text:
                before = frame.text[: frame.text.index(RECORDING_MARKER)]
                if before:
                    await self.push_frame(LLMTextFrame(before), direction)
                self._second_marker_seen = True
            else:
                await self.push_frame(frame, direction)
            return

        # --- Recording mode: accumulate text and start playback ASAP ---
        if self._mode == "recording":
            text = frame.text
            if TTS_MARKER in text:
                text = text[: text.index(TTS_MARKER)]
                self._second_marker_seen = True
            self._recording_id_buffer += text
            if not self._recording_playback_started:
                buf = self._recording_id_buffer.lstrip()
                if " " in buf:
                    recording_id = buf.split()[0]
                    self._recording_playback_started = True
                    await self._play_recording(recording_id)
            return

        # --- Detection mode: buffer until marker found ---
        self._frame_buffer.append((frame, direction))
        buffered_text = self._buffered_text()

        # Check for recording marker (●)
        if RECORDING_MARKER in buffered_text:
            self._mode = "recording"
            marker_end = buffered_text.index(RECORDING_MARKER) + len(RECORDING_MARKER)

            # Extract recording_id from post-marker text (don't push frames)
            cumulative = 0
            for buf_frame, buf_dir in self._frame_buffer:
                frame_start = cumulative
                cumulative += len(buf_frame.text)

                # Capture any recording_id text after the marker
                if cumulative > marker_end:
                    offset = max(marker_end - frame_start, 0)
                    remaining = buf_frame.text[offset:]
                    if not self._recording_id_buffer and remaining.startswith(" "):
                        remaining = remaining[1:]
                    self._recording_id_buffer += remaining

            self._frame_buffer = []
            return

        # Check for TTS marker (▸)
        if TTS_MARKER in buffered_text:
            self._mode = "tts"
            marker_end = buffered_text.index(TTS_MARKER) + len(TTS_MARKER)

            # Push buffered frames — skip_tts for marker portion, normal for the rest
            cumulative = 0
            for buf_frame, buf_dir in self._frame_buffer:
                frame_start = cumulative
                cumulative += len(buf_frame.text)

                if cumulative <= marker_end:
                    # Entirely within marker portion — suppress TTS
                    buf_frame.skip_tts = True
                    await self.push_frame(buf_frame, buf_dir)
                elif frame_start >= marker_end:
                    # Entirely after marker — normal TTS speech
                    if frame_start == marker_end and buf_frame.text.startswith(" "):
                        buf_frame.text = buf_frame.text[1:]
                    if buf_frame.text:
                        await self.push_frame(buf_frame, buf_dir)
                else:
                    # Frame spans the marker boundary — split
                    offset = marker_end - frame_start
                    original_text = buf_frame.text
                    buf_frame.text = original_text[:offset]
                    buf_frame.skip_tts = True
                    await self.push_frame(buf_frame, buf_dir)

                    tts_text = original_text[offset:]
                    if tts_text.startswith(" "):
                        tts_text = tts_text[1:]
                    if tts_text:
                        await self.push_frame(LLMTextFrame(tts_text), buf_dir)

            self._frame_buffer = []
            return

        # Neither marker found yet — keep buffering (should arrive very soon)

    # ------------------------------------------------------------------
    # End-of-response handling
    # ------------------------------------------------------------------

    async def _handle_response_end(
        self, frame: LLMFullResponseEndFrame, direction: FrameDirection
    ):
        if self._mode == "recording":
            full_text = self._recording_id_buffer.strip()
            if full_text:
                recording_id = full_text.split()[0]

                # Push full text (marker + id + transcript) for assistant context
                await self.push_frame(
                    TTSTextFrame(
                        text=RECORDING_MARKER + self._recording_id_buffer,
                        aggregated_by="recording_router",
                    )
                )

                # Fallback: if response ended before a space arrived (no transcript)
                if not self._recording_playback_started:
                    await self._play_recording(recording_id)
            else:
                logger.warning(
                    "RecordingRouterProcessor: recording mode but empty recording_id"
                )

        elif self._mode is None and self._frame_buffer:
            # Graceful degradation: no marker detected, pass text to TTS as-is
            logger.warning(
                "RecordingRouterProcessor: no response mode marker found, "
                "passing text to TTS as-is"
            )
            for buf_frame, buf_dir in self._frame_buffer:
                await self.push_frame(buf_frame, buf_dir)

        self._reset()
        await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    # Audio playback
    # ------------------------------------------------------------------

    async def _play_recording(self, recording_id: str):
        """Fetch recording audio and push TTSStarted → TTSAudioRaw → TTSStopped.

        The transport handles chunking automatically. The Started/Stopped
        frames ensure downstream processors (transport, audio buffer, observers)
        treat this as a proper TTS utterance.
        """
        logger.info(f"Playing pre-recorded audio: {recording_id}")

        result = await self._fetch_recording_audio(recording_id=recording_id)
        if not result:
            logger.warning(
                f"Failed to fetch recording {recording_id}, no audio will play"
            )
            return

        context_id = str(uuid.uuid4())
        await self.push_frame(TTSStartedFrame(context_id=context_id))
        await self.push_frame(
            TTSAudioRawFrame(
                audio=result.audio,
                sample_rate=self._audio_sample_rate,
                num_channels=1,
                context_id=context_id,
            )
        )
        await self.push_frame(TTSStoppedFrame(context_id=context_id))

        duration_secs = len(result.audio) / (self._audio_sample_rate * 2)
        logger.debug(
            f"Finished pushing recording {recording_id} "
            f"({len(result.audio)} bytes, {duration_secs:.1f}s)"
        )

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _buffered_text(self) -> str:
        """Return concatenated text from the frame buffer."""
        return "".join(f.text for f, _ in self._frame_buffer)

    def _reset(self):
        """Reset per-response state."""
        self._frame_buffer = []
        self._mode = None
        self._recording_id_buffer = ""
        self._recording_playback_started = False
        self._second_marker_seen = False
