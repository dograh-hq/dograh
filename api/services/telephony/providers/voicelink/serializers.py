"""VoiceLink Media Streams WebSocket protocol frame serializer.

VoiceLink speaks a Twilio-media-streams-style JSON protocol but with
G.711 A-law (``audio/alaw``) at 8 kHz instead of µ-law, and snake_case
identifiers (``stream_sid``/``call_sid``) in the start event. There is no
VoiceLink serializer in pipecat, so this one lives provider-local and
reuses pipecat's A-law converters (``alaw_to_pcm``/``pcm_to_alaw``).

Wire protocol:

- VoiceLink → us: ``connected``, ``start`` (handled by the provider's
  ``handle_websocket`` before the pipeline starts), ``media`` (base64
  A-law payload), ``mark``, ``stop``, ``transfer``, ``dtmf``.
- Us → VoiceLink: ``media`` (base64 A-law payload), ``mark``, ``clear``
  (barge-in). Frames must stay well under 2 s of audio — pipecat's output
  transport already chunks audio into 10 ms multiples.
"""

import base64
import json

from loguru import logger
from pydantic import BaseModel

from pipecat.audio.dtmf.types import KeypadEntry
from pipecat.audio.utils import alaw_to_pcm, create_stream_resampler, pcm_to_alaw
from pipecat.frames.frames import (
    AudioRawFrame,
    Frame,
    InputAudioRawFrame,
    InputDTMFFrame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer


class VoiceLinkFrameSerializer(FrameSerializer):
    """Serializer for the VoiceLink Media Streams WebSocket protocol.

    Converts between Pipecat frames and VoiceLink's WebSocket messages:
    8 kHz A-law audio in both directions, ``clear`` on interruption for
    barge-in, and DTMF events.
    """

    class InputParams(BaseModel):
        """Configuration parameters for VoiceLinkFrameSerializer.

        Parameters:
            voicelink_sample_rate: Sample rate used by VoiceLink, defaults to 8000 Hz.
            sample_rate: Optional override for pipeline input sample rate.
        """

        voicelink_sample_rate: int = 8000
        sample_rate: int | None = None

    def __init__(
        self,
        stream_sid: str,
        call_sid: str | None = None,
        params: InputParams | None = None,
    ):
        """Initialize the VoiceLinkFrameSerializer.

        Args:
            stream_sid: The VoiceLink media stream id from the start event.
            call_sid: The associated VoiceLink call id (optional).
            params: Configuration parameters.
        """
        self._params = params or VoiceLinkFrameSerializer.InputParams()

        self._stream_sid = stream_sid
        self._call_sid = call_sid

        self._voicelink_sample_rate = self._params.voicelink_sample_rate
        self._sample_rate = 0  # Pipeline input rate

        self._input_resampler = create_stream_resampler()
        self._output_resampler = create_stream_resampler()

    async def setup(self, frame: StartFrame):
        """Sets up the serializer with pipeline configuration.

        Args:
            frame: The StartFrame containing pipeline configuration.
        """
        self._sample_rate = self._params.sample_rate or frame.audio_in_sample_rate

    async def serialize(self, frame: Frame) -> str | bytes | None:
        """Serializes a Pipecat frame to VoiceLink WebSocket format.

        Args:
            frame: The Pipecat frame to serialize.

        Returns:
            Serialized data as string, or None if the frame isn't handled.
        """
        if isinstance(frame, InterruptionFrame):
            answer = {"event": "clear", "stream_sid": self._stream_sid}
            return json.dumps(answer)
        elif isinstance(frame, AudioRawFrame):
            data = frame.audio

            # Output: convert PCM at the frame's rate to 8 kHz A-law
            serialized_data = await pcm_to_alaw(
                data,
                frame.sample_rate,
                self._voicelink_sample_rate,
                self._output_resampler,
            )
            if serialized_data is None or len(serialized_data) == 0:
                # Ignoring in case we don't have audio
                return None

            payload = base64.b64encode(serialized_data).decode("utf-8")
            answer = {
                "event": "media",
                "stream_sid": self._stream_sid,
                "media": {"payload": payload},
            }

            return json.dumps(answer)
        elif isinstance(
            frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)
        ):
            return json.dumps(frame.message)

        # Return None for unhandled frames
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        """Deserializes VoiceLink WebSocket data to Pipecat frames.

        Args:
            data: The raw WebSocket data from VoiceLink.

        Returns:
            A Pipecat frame corresponding to the VoiceLink event, or None
            if unhandled.
        """
        message = json.loads(data)
        event = message.get("event")

        if event == "media":
            payload_base64 = message["media"]["payload"]
            payload = base64.b64decode(payload_base64)

            # Input: convert VoiceLink's 8 kHz A-law to PCM at pipeline rate
            deserialized_data = await alaw_to_pcm(
                payload,
                self._voicelink_sample_rate,
                self._sample_rate,
                self._input_resampler,
            )
            if deserialized_data is None or len(deserialized_data) == 0:
                # Ignoring in case we don't have audio
                return None

            return InputAudioRawFrame(
                audio=deserialized_data,
                num_channels=1,
                sample_rate=self._sample_rate,
            )
        elif event == "dtmf":
            digit = message.get("dtmf", {}).get("digit")

            try:
                return InputDTMFFrame(KeypadEntry(digit))
            except ValueError:
                # Handle case where string doesn't match any enum value
                return None
        elif event == "transfer":
            # Native VoiceLink call routing — informational for now.
            logger.info(
                f"VoiceLink transfer event for stream {self._stream_sid}: "
                f"target={message.get('target')}"
            )
            return None
        else:
            # connected / start are consumed pre-pipeline; mark/stop are
            # informational.
            return None
