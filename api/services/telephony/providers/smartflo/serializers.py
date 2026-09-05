"""Smartflo Frame Serializer.

Translates between Smartflo Voice Streaming WebSocket messages (8kHz μ-law / A-law Base64)
and Pipecat Linear PCM frames with audio resampling.
"""

import base64
import json
from typing import Optional

from loguru import logger
from pipecat.audio.dtmf.types import KeypadEntry
from pipecat.audio.utils import (
    alaw_to_pcm,
    create_stream_resampler,
    pcm_to_alaw,
    pcm_to_ulaw,
    ulaw_to_pcm,
)
from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InputDTMFFrame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer


class SmartfloFrameSerializer(FrameSerializer):
    """Pipecat FrameSerializer for Tata Smartflo Voice Streaming."""

    def __init__(
        self,
        stream_sid: Optional[str] = None,
        call_sid: Optional[str] = None,
        sample_rate: int = 8000,
        encoding: str = "audio/x-mulaw",
        binary_mode: bool = False,
    ):
        super().__init__()
        self._stream_sid = stream_sid or "smartflo_stream"
        self._call_sid = call_sid
        self._smartflo_sample_rate = 8000
        self._sample_rate = sample_rate or 8000
        self._encoding = (encoding or "audio/x-mulaw").lower()
        self._is_alaw = "alaw" in self._encoding
        self._binary_mode = binary_mode

        self._input_resampler = create_stream_resampler()
        self._output_resampler = create_stream_resampler()

    def set_stream_sid(self, stream_sid: str) -> None:
        self._stream_sid = stream_sid

    def set_call_sid(self, call_sid: str) -> None:
        self._call_sid = call_sid

    def set_encoding(self, encoding: str) -> None:
        self._encoding = (encoding or "audio/x-mulaw").lower()
        self._is_alaw = "alaw" in self._encoding

    async def setup(self, frame: StartFrame):
        """Sets up the serializer with pipeline configuration."""
        self._sample_rate = frame.audio_in_sample_rate or self._sample_rate

    async def serialize(self, frame: Frame) -> str | bytes | None:
        """Serialize outbound Pipecat frames for Smartflo."""
        if isinstance(frame, InterruptionFrame):
            # Tell Smartflo to immediately clear buffered playback audio
            return json.dumps({
                "event": "clear",
                "streamSid": self._stream_sid,
            })

        if isinstance(frame, (EndFrame, CancelFrame)):
            logger.debug(f"[Smartflo] Serializing {type(frame).__name__} -> stop event")
            return json.dumps({
                "event": "stop",
                "streamSid": self._stream_sid,
            })

        if isinstance(frame, AudioRawFrame):
            data = frame.audio
            if not data:
                return None

            if self._binary_mode:
                return data

            # Convert Linear PCM from pipeline to 8kHz μ-law (or A-law) for Smartflo
            if self._is_alaw:
                encoded_bytes = await pcm_to_alaw(
                    data, frame.sample_rate, self._smartflo_sample_rate, self._output_resampler
                )
            else:
                encoded_bytes = await pcm_to_ulaw(
                    data, frame.sample_rate, self._smartflo_sample_rate, self._output_resampler
                )

            if not encoded_bytes:
                return None

            b64_payload = base64.b64encode(encoded_bytes).decode("ascii")
            msg = {
                "event": "media",
                "streamSid": self._stream_sid,
                "media": {
                    "payload": b64_payload,
                },
            }
            return json.dumps(msg)

        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            if self.should_ignore_frame(frame):
                return None
            return json.dumps(frame.message)

        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        """Deserialize inbound Smartflo WebSocket messages into Pipecat frames."""
        if isinstance(data, bytes):
            # Check if this is a JSON payload delivered as bytes
            stripped = data.strip()
            if stripped.startswith(b"{") and stripped.endswith(b"}"):
                try:
                    data = stripped.decode("utf-8")
                except UnicodeDecodeError:
                    self._binary_mode = True
                    return InputAudioRawFrame(
                        audio=data,
                        num_channels=1,
                        sample_rate=self._sample_rate,
                    )
            else:
                self._binary_mode = True
                return InputAudioRawFrame(
                    audio=data,
                    num_channels=1,
                    sample_rate=self._sample_rate,
                )

        # Handle text JSON messages
        try:
            msg = json.loads(data)
        except Exception:
            return None

        event = msg.get("event")

        if event == "media":
            media_info = msg.get("media", {})
            payload_b64 = media_info.get("payload")
            if not payload_b64:
                return None

            try:
                raw_bytes = base64.b64decode(payload_b64)
            except Exception as e:
                logger.warning(f"[Smartflo] Invalid base64 audio payload: {e}")
                return None

            if not raw_bytes:
                return None

            # Convert 8kHz μ-law (or A-law) to Linear PCM at pipeline input rate
            sample_rate = self._sample_rate or self._smartflo_sample_rate
            if self._is_alaw:
                pcm_data = await alaw_to_pcm(
                    raw_bytes, self._smartflo_sample_rate, sample_rate, self._input_resampler
                )
            else:
                pcm_data = await ulaw_to_pcm(
                    raw_bytes, self._smartflo_sample_rate, sample_rate, self._input_resampler
                )

            if not pcm_data:
                return None

            return InputAudioRawFrame(
                audio=pcm_data,
                num_channels=1,
                sample_rate=sample_rate,
            )

        if event == "dtmf":
            digit = msg.get("dtmf", {}).get("digit")
            if digit is not None:
                try:
                    return InputDTMFFrame(KeypadEntry(str(digit)))
                except ValueError:
                    return None

        if event == "start":
            start_info = msg.get("start", {})
            if isinstance(start_info, dict):
                media_format = start_info.get("mediaFormat", {})
                if isinstance(media_format, dict):
                    enc = media_format.get("encoding")
                    if enc:
                        self.set_encoding(enc)
                stream_sid = start_info.get("streamSid") or msg.get("streamSid")
                if stream_sid:
                    self._stream_sid = stream_sid
            return None

        if event in ("stop", "close", "hangup"):
            logger.info(f"[Smartflo] Inbound stream stop requested: {event}")
            return EndFrame()

        return None
