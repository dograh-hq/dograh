"""Exotel frame serializer.

Exotel's WebSocket streaming protocol is Twilio Media Streams-compatible:
  - Incoming audio: {"event":"media","stream_sid":"...","media":{"payload":"<base64-mulaw>"}}
  - Outgoing audio: {"event":"media","streamSid":"...","media":{"payload":"<base64-mulaw>"}}
  - Clear/interrupt: {"event":"clear","streamSid":"..."}

We subclass TwilioFrameSerializer to inherit the correct encode/decode logic
and override _hang_up_call to hit Exotel's REST API instead of Twilio's.
"""

import json

import aiohttp
from loguru import logger
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer

import base64


class ExotelFrameSerializer(FrameSerializer):
    """Serializer for Exotel's Twilio-compatible WebSocket streaming protocol.

    Exotel sends/receives audio using the same JSON envelope as Twilio Media Streams:
      Incoming: {"event":"media","media":{"payload":"<base64-pcm16>"},"stream_sid":"..."}
      Outgoing: {"event":"media","streamSid":"...","media":{"payload":"<base64-pcm16>"}}
      Clear:    {"event":"clear","streamSid":"..."}

    Auto hang-up hits Exotel's REST API (DELETE /v1/Accounts/{sid}/Calls/{call_sid}/).
    """

    class InputParams(FrameSerializer.InputParams):
        sample_rate_hz: int = 8000
        sample_rate: int | None = None
        auto_hang_up: bool = False  # Exotel hangs up itself; safe to disable

    def __init__(
        self,
        stream_id: str,         # Exotel stream_sid
        call_id: str | None = None,
        auth_id: str | None = None,       # Exotel api_key
        auth_token: str | None = None,    # Exotel api_token
        account_sid: str | None = None,   # Exotel account_sid for REST hangup
        subdomain: str = "api.exotel.com",
        params: "ExotelFrameSerializer.InputParams | None" = None,
    ):
        params = params or ExotelFrameSerializer.InputParams()
        super().__init__(params)
        self._params: ExotelFrameSerializer.InputParams = params

        self._stream_sid = stream_id
        self._call_sid = call_id
        self._api_key = auth_id
        self._api_token = auth_token
        self._account_sid = account_sid
        self._subdomain = subdomain
        self._exotel_sample_rate = params.sample_rate_hz
        self._sample_rate = 0  # Set in setup()

        self._input_resampler = create_stream_resampler()
        self._output_resampler = create_stream_resampler()
        self._hangup_attempted = False

    async def setup(self, frame: StartFrame):
        self._sample_rate = self._params.sample_rate or frame.audio_in_sample_rate

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, (EndFrame, CancelFrame)):
            if self._params.auto_hang_up and not self._hangup_attempted:
                self._hangup_attempted = True
                await self._hang_up_call()
            return None

        elif isinstance(frame, InterruptionFrame):
            return json.dumps({"event": "clear", "streamSid": self._stream_sid})

        elif isinstance(frame, AudioRawFrame):
            serialized_data = await self._output_resampler.resample(
                frame.audio,
                frame.sample_rate,
                self._exotel_sample_rate
            )
            if not serialized_data:
                return None
            payload = base64.b64encode(serialized_data).decode("utf-8")
            return json.dumps({
                "event": "media",
                "streamSid": self._stream_sid,
                "media": {"payload": payload},
            })

        elif isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            if self.should_ignore_frame(frame):
                return None
            return json.dumps(frame.message)

        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            logger.warning(f"[Exotel] Failed to parse WebSocket message: {data[:200]}")
            return None

        event = message.get("event", "")

        if event == "media":
            media = message.get("media", {})
            payload_b64 = media.get("payload")
            if not payload_b64:
                return None

            payload = base64.b64decode(payload_b64)
            deserialized = await self._input_resampler.resample(
                payload,
                self._exotel_sample_rate,
                self._sample_rate
            )
            if not deserialized:
                return None
            return InputAudioRawFrame(
                audio=deserialized, num_channels=1, sample_rate=self._sample_rate
            )

        # Ignore start/stop/connected/mark events silently
        return None

    async def _hang_up_call(self):
        """Hang up via Exotel REST API."""
        if not (self._api_key and self._api_token and self._account_sid and self._call_sid):
            logger.warning("[Exotel] Cannot hang up — missing credentials or call_sid")
            return
        try:
            endpoint = (
                f"https://{self._api_key}:{self._api_token}@{self._subdomain}"
                f"/v1/Accounts/{self._account_sid}/Calls/{self._call_sid}/"
            )
            async with aiohttp.ClientSession() as session:
                async with session.delete(endpoint) as resp:
                    if resp.status in (200, 204):
                        logger.debug(f"[Exotel] Hung up call {self._call_sid}")
                    else:
                        body = await resp.text()
                        logger.warning(
                            f"[Exotel] Hangup returned {resp.status}: {body[:200]}"
                        )
        except Exception as e:
            logger.error(f"[Exotel] Hangup failed: {e}")


__all__ = ["ExotelFrameSerializer"]
