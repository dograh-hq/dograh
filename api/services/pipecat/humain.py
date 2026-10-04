"""Humain Voice SDK 0.18.0 adapters. Keys stay in the backend."""

import asyncio

from humain_voice import stt, tts

from pipecat.audio.resamplers.soxr_stream_resampler import SOXRStreamAudioResampler
from pipecat.frames.frames import (
    ErrorFrame, InterimTranscriptionFrame, TranscriptionFrame, TTSAudioRawFrame,
    TTSStartedFrame, TTSStoppedFrame,
)
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601


def voice_client(config):
    return tts.TTSClient(api_url=config.base_url, api_key=config.api_key, api_path=config.api_path)


async def list_humain_voices(config):
    async with asyncio.timeout(15), voice_client(config) as client:
        voices = await client.list_voices(timeout_seconds=5)
    return [
        {
            "voice_id": voice["id"], "name": voice["label"],
            "gender": (voice.get("profile") or {}).get("speaker", {}).get("gender"),
            "accent": (voice.get("profile") or {}).get("speaker", {}).get("dialect"),
            "language": ", ".join((voice.get("profile") or {}).get("languages", [])),
        }
        for voice in voices
    ]


class HumainTTSService(TTSService):
    def __init__(self, *, config, **kwargs):
        super().__init__(sample_rate=24000, settings=TTSSettings(model=config.model, voice=config.voice), **kwargs)
        self._config = config

    async def run_tts(self, text, context_id=None):
        # Per-request ownership also closes sockets on interruption/cancellation.
        yield TTSStartedFrame(context_id=context_id)
        try:
            async with asyncio.timeout(40), voice_client(self._config) as client:
                async for response in client.synthesize_stream(
                    text, voice_id=self._settings.voice, model=self._settings.model,
                    timeout_seconds=30,
                ):
                    if response.audio:
                        yield TTSAudioRawFrame(audio=response.audio, sample_rate=24000, num_channels=1, context_id=context_id)
        except Exception as exc:
            # Provider messages can contain submitted text. Expose only the class.
            yield ErrorFrame(error=f"Humain TTS failed ({type(exc).__name__})")
        yield TTSStoppedFrame(context_id=context_id)


class HumainSTTService(STTService):
    def __init__(self, *, config, **kwargs):
        super().__init__(sample_rate=16000, settings=STTSettings(model="realtime"), **kwargs)
        self._config = config
        self._client = None
        self._stream = None
        self._responses = asyncio.Queue(maxsize=256)
        self._resampler = SOXRStreamAudioResampler()
        self._input_rate = 16000
        self._response_task = None

    async def start(self, frame):
        await super().start(frame)
        self._input_rate = frame.audio_in_sample_rate
        self._client = stt.RealtimeClient(api_url=self._config.base_url, api_key=self._config.api_key, api_path=self._config.api_path)
        try:
            await asyncio.wait_for(self._client.connect(), timeout=10)
            self._stream = await self._client.start_stream(
                language=stt.Language(self._config.language),
                on_response=self._receive,
                on_error=self._receive_error,
            )
            self._response_task = asyncio.create_task(self._consume())
        except BaseException:
            await self._close()
            raise

    def _receive(self, response):
        try:
            self._responses.put_nowait(response)
        except asyncio.QueueFull:
            self._receive_error(None)

    def _receive_error(self, _error):
        # Drop stale provisional text to make room for an explicit failure.
        if self._responses.full():
            self._responses.get_nowait()
            self._responses.task_done()
        self._responses.put_nowait(ErrorFrame(error="Humain STT stream failed"))

    async def _consume(self):
        while True:
            response = await self._responses.get()
            try:
                if isinstance(response, ErrorFrame):
                    await self.push_frame(response)
                elif response.transcription:
                    frame_type = TranscriptionFrame if response.is_final or response.is_speech_final else InterimTranscriptionFrame
                    await self.push_frame(frame_type(text=response.transcription, user_id=self._user_id, timestamp=time_now_iso8601()))
            finally:
                self._responses.task_done()

    async def run_stt(self, audio):
        if self._stream is None:
            yield ErrorFrame(error="Humain STT is not connected")
            return
        try:
            if self._input_rate != 16000:
                audio = await self._resampler.resample(audio, self._input_rate, 16000)
            await asyncio.wait_for(self._stream.send(audio), timeout=10)
            yield None
        except Exception as exc:
            yield ErrorFrame(error=f"Humain STT send failed ({type(exc).__name__})")

    async def _close(self, graceful=False):
        try:
            if self._stream is not None and graceful:
                await asyncio.wait_for(self._stream.close(timeout_seconds=2), timeout=3)
                # Allow final callbacks to enter the consumer before stopping it.
                if self._response_task:
                    await asyncio.wait_for(self._responses.join(), timeout=2)
        finally:
            if self._response_task:
                self._response_task.cancel()
                await asyncio.gather(self._response_task, return_exceptions=True)
                self._response_task = None
            if self._client:
                await asyncio.wait_for(self._client.disconnect(), timeout=5)
                self._client = None
            self._stream = None

    async def stop(self, frame):
        try:
            await self._close(graceful=True)
        finally:
            await super().stop(frame)

    async def cancel(self, frame):
        try:
            await self._close()
        finally:
            await super().cancel(frame)

    async def cleanup(self):
        try:
            await self._close()
        finally:
            await super().cleanup()
