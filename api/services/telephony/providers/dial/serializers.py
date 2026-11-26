"""Frame serializer for Dial's Self-Hosted audio protocol.

Wire format (https://docs.getdial.ai/api-reference/self-hosted-audio-protocol):
JSON text frames discriminated by ``type``; audio rides base64-encoded inside
``media`` frames. The integration configures G.711 μ-law at 8 kHz in both
directions, so this serializer only speaks ``mulaw_8000``.

Dial → us: ``media``, ``dtmf``, ``mark`` (echo), ``duration_warning``,
``call_ended``, ``ping_pong``. ``ping_pong`` never reaches the serializer —
the socket wrapper in ``websocket.py`` answers it before pipecat reads.

Us → Dial: ``media``, ``clear`` (barge-in), ``end_call`` (hang up).
"""

import base64
import json

from loguru import logger
from pipecat.audio import g711
from pipecat.audio.dtmf.types import KeypadEntry
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
)
from pipecat.serializers.base_serializer import FrameSerializer

DIAL_SAMPLE_RATE = 8000
DIAL_AUDIO_FORMAT = "mulaw_8000"


class DialFrameSerializer(FrameSerializer):
    """Converts between pipecat frames and Dial Self-Hosted audio frames."""

    class InputParams(FrameSerializer.InputParams):
        """Dial wire rates are fixed by the configured ``mulaw_8000`` format."""

        audio_in_sample_rate: int = DIAL_SAMPLE_RATE
        audio_out_sample_rate: int = DIAL_SAMPLE_RATE

    def __init__(self, call_id: str, params: InputParams | None = None):
        params = params or DialFrameSerializer.InputParams()
        super().__init__(params)
        self._call_id = call_id
        self._hangup_sent = False

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, (EndFrame, CancelFrame)):
            # Pipecat writes the stop frame before closing the socket, so the
            # hangup travels in-band; Dial refuses reconnects after end_call.
            if self._hangup_sent:
                return None
            self._hangup_sent = True
            logger.debug(f"Sending end_call for Dial call {self._call_id}")
            return json.dumps({"type": "end_call"})

        if isinstance(frame, InterruptionFrame):
            await self._reset_audio_out()
            return json.dumps({"type": "clear"})

        if isinstance(frame, AudioRawFrame):
            data = await self._resample_audio_out(frame)
            if not data:
                return None
            payload = base64.b64encode(g711.pcm_to_ulaw(data)).decode("utf-8")
            return json.dumps({"type": "media", "payload": payload})

        if isinstance(
            frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)
        ):
            if self.should_ignore_frame(frame):
                return None
            return json.dumps(frame.message)

        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        try:
            message = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Dial call {self._call_id}: ignoring non-JSON frame")
            return None

        if not isinstance(message, dict):
            return None

        message_type = message.get("type")

        if message_type == "media":
            payload_base64 = message.get("payload")
            if not payload_base64:
                return None
            audio = await self._resample_audio_in(
                g711.ulaw_to_pcm(base64.b64decode(payload_base64))
            )
            if not audio:
                return None
            return InputAudioRawFrame(
                audio=audio, num_channels=1, sample_rate=self._sample_rate
            )

        if message_type == "dtmf":
            try:
                return InputDTMFFrame(KeypadEntry(message.get("digit")))
            except ValueError:
                return None

        if message_type == "call_ended":
            # The socket closes right after; pipecat's disconnect handling
            # ends the pipeline. Logged for the run's diagnostics only.
            logger.info(
                f"Dial call {self._call_id} ended: reason={message.get('reason')}"
            )
            return None

        if message_type == "duration_warning":
            logger.info(
                f"Dial call {self._call_id} nears its duration cap: "
                f"{message.get('seconds_remaining')}s remaining"
            )
            return None

        return None
