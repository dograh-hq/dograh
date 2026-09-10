"""Papi Voip frame serializer — PCM s16le mono @ 16 kHz (1920-byte frames)."""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.serializers.call_strategies import HangupStrategy
from pydantic import BaseModel


PAPI_FRAME_BYTES = 1920  # 60 ms * 16000 Hz * 2 bytes (s16le mono)
PAPI_SAMPLE_RATE = 16000


class PapiVoipFrameSerializer(FrameSerializer):
    """Bridge Dograh/Pipecat audio to Papi GO Cloud voice stream frames.

    Papi documents each binary WebSocket message as exactly 1920 bytes of
    PCM mono s16le @ 16 kHz. Undersized trailing audio is zero-padded on
    hangup/end so the peer still receives a valid last frame.
    """

    class InputParams(FrameSerializer.InputParams):
        papi_sample_rate: int = PAPI_SAMPLE_RATE
        sample_rate: int | None = None
        frame_bytes: int = PAPI_FRAME_BYTES

    def __init__(
        self,
        *,
        call_id: str,
        base_url: str,
        api_key: str,
        instance_id: str,
        hangup_strategy: Optional[HangupStrategy] = None,
        params: InputParams | None = None,
    ):
        params = params or PapiVoipFrameSerializer.InputParams()
        super().__init__(params)
        self._params: PapiVoipFrameSerializer.InputParams = params
        self._call_id = call_id
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._instance_id = instance_id
        self._hangup_strategy = hangup_strategy

        self._papi_sample_rate = self._params.papi_sample_rate
        self._sample_rate = 0
        self._frame_bytes = self._params.frame_bytes
        self._out_buffer = bytearray()
        self._hangup_sent = False

        self._input_resampler = create_stream_resampler(
            clear_after_secs=self._params.resampler_clear_after_secs
        )
        self._output_resampler = create_stream_resampler(
            clear_after_secs=self._params.resampler_clear_after_secs
        )

    async def setup(self, frame: StartFrame):
        self._sample_rate = self._params.sample_rate or frame.audio_in_sample_rate

    def _hangup_context(self) -> dict[str, Any]:
        return {
            "call_id": self._call_id,
            "base_url": self._base_url,
            "api_key": self._api_key,
            "instance_id": self._instance_id,
        }

    async def _maybe_hangup(self) -> None:
        if self._hangup_sent or not self._hangup_strategy:
            return
        self._hangup_sent = True
        try:
            await self._hangup_strategy.execute_hangup(self._hangup_context())
        except Exception as e:
            logger.warning(f"[Papi Voip] hangup strategy failed: {e}")

    def _drain_frames(self, flush: bool = False) -> list[bytes]:
        frames: list[bytes] = []
        while len(self._out_buffer) >= self._frame_bytes:
            chunk = bytes(self._out_buffer[: self._frame_bytes])
            del self._out_buffer[: self._frame_bytes]
            frames.append(chunk)
        if flush and self._out_buffer:
            padded = bytes(self._out_buffer) + bytes(
                self._frame_bytes - len(self._out_buffer)
            )
            self._out_buffer.clear()
            frames.append(padded)
        return frames

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, (EndFrame, CancelFrame)):
            # Flush remaining PCM frames and hang up the Papi call.
            pending = self._drain_frames(flush=True)
            await self._maybe_hangup()
            if pending:
                return b"".join(pending)
            return None

        if isinstance(frame, AudioRawFrame):
            data = await self._output_resampler.resample(
                frame.audio, frame.sample_rate, self._papi_sample_rate
            )
            if not data:
                return None
            self._out_buffer.extend(data)
            frames = self._drain_frames(flush=False)
            if not frames:
                return None
            return b"".join(frames)

        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            if not data:
                return None
            # Ignore non-audio control blobs; Papi may send odd sizes when idle.
            payload = data
            if len(payload) != self._frame_bytes and len(payload) % 2 != 0:
                return None

            deserialized = await self._input_resampler.resample(
                payload, self._papi_sample_rate, self._sample_rate or self._papi_sample_rate
            )
            if not deserialized:
                return None
            return InputAudioRawFrame(
                audio=deserialized,
                num_channels=1,
                sample_rate=self._sample_rate or self._papi_sample_rate,
            )

        # Text = handshake / control JSON from Papi ("ready", frameBytes, ...)
        try:
            message = json.loads(data)
            if message.get("ready"):
                logger.info(
                    f"[Papi Voip] stream ready call_id={self._call_id} "
                    f"frameBytes={message.get('frameBytes')} "
                    f"sampleRate={message.get('sampleRate')}"
                )
            else:
                logger.debug(f"[Papi Voip] control message: {message}")
        except json.JSONDecodeError:
            logger.debug(f"[Papi Voip] ignoring non-JSON text frame: {data!r}")
        return None
