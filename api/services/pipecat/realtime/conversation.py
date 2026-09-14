"""Conversation behavior shared by Dograh's realtime service adapters."""

from dataclasses import replace

from loguru import logger

from pipecat.audio.resamplers.base_audio_resampler import BaseAudioResampler
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection


class RealtimeConversationMixin:
    """Share greeting and mute policy without owning provider session state.

    Providers implement ``_handle_initial_greeting``, ``_handle_context`` and
    ``_open_after_prerecorded_greeting``. The first two mark
    ``_handled_initial_context`` themselves once they accept the opening turn;
    the third does not, because ``handle_prerecorded_greeting`` has already
    set it on the provider's behalf. That flag belongs to the conversation and
    survives node reconnects. Session readiness, turn detection, and
    reconnects remain upstream concerns.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._user_is_muted = False
        # The engine assigns _context before the first context frame arrives.
        self._handled_initial_context = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, (UserMuteStartedFrame, UserMuteStoppedFrame)):
            self._user_is_muted = isinstance(frame, UserMuteStartedFrame)
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSSpeakFrame):
            # Realtime owns speech output. Only the opening TTS trigger is
            # meaningful; later prompts use LLMMessagesAppendFrame instead.
            if self._handled_initial_context:
                logger.debug(f"{self}: ignoring text speech after the opening turn")
                return
            text = frame.text.strip() if frame.text else ""
            if text:
                await self._handle_initial_greeting(self._context, text)
            else:
                await self._handle_context(self._context)
            return
        await super().process_frame(frame, direction)

    async def handle_prerecorded_greeting(
        self, context, transcript: str | None
    ) -> None:
        """Open the conversation after a recording has greeted the caller.

        An audio greeting is queued straight to the output transport, so the
        opening ``TTSSpeakFrame`` never reaches the service. The session still
        has to be seeded and start accepting caller audio — providers that gate
        input on the opening turn (Gemini Live) otherwise drop every caller
        frame — but nothing may be generated, because the caller has already
        been greeted.

        ``transcript`` is the greeting the caller just heard, seeded as an
        assistant turn so the model does not greet them a second time. It
        arrives here rather than through ``LLMContext`` because the recording
        is still playing: the aggregator commits it once playback drains,
        after this seed has been sent.
        """
        if self._handled_initial_context:
            return
        if context is None:
            logger.warning(
                f"{self}: received prerecorded greeting before context was set"
            )
            return
        self._handled_initial_context = True
        self._context = context
        await self._open_after_prerecorded_greeting(transcript)

    async def _open_after_prerecorded_greeting(self, transcript: str | None) -> None:
        """Seed the spoken greeting, accept caller audio, and stay silent."""
        raise NotImplementedError

    async def _send_user_audio(self, frame: InputAudioRawFrame):
        await super()._send_user_audio(await self._prepare_user_audio(frame))

    async def _prepare_user_audio(self, frame: InputAudioRawFrame):
        return await self._prepare_audio_frame(frame)

    async def _prepare_audio_frame(
        self,
        frame: InputAudioRawFrame,
        *,
        sample_rate: int | None = None,
        resampler: BaseAudioResampler | None = None,
        silence_byte: int = 0,
    ) -> InputAudioRawFrame:
        """Keep the input clock running with silence while the caller is muted.

        Mask before resampling so muted speech cannot enter its history, and
        after resampling to remove any previously buffered caller audio. Copy
        frames so recording and other consumers retain the original audio.
        """
        muted = self._user_is_muted
        audio = bytes([silence_byte]) * len(frame.audio) if muted else frame.audio
        target_rate = sample_rate or frame.sample_rate
        if resampler is not None and frame.sample_rate != target_rate:
            audio = await resampler.resample(audio, frame.sample_rate, target_rate)
        if muted or self._user_is_muted:
            audio = bytes([silence_byte]) * len(audio)
        if audio is frame.audio and target_rate == frame.sample_rate:
            return frame
        return replace(frame, audio=audio, sample_rate=target_rate)
