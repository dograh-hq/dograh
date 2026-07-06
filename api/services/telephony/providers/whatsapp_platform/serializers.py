"""WhatsApp Platform frame serializer.

The bridge (Node.js dograh-bridge.js) sends and receives raw binary
16kHz Linear PCM frames over the WebSocket — exactly the same wire
format as Asterisk's native PCM stream.

We re-use Pipecat's built-in RawAudioFrameSerializer which handles
binary PCM frames with no framing headers.
"""

from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.frames.frames import AudioRawFrame, InputAudioRawFrame, OutputAudioRawFrame, Frame
from loguru import logger
import struct


class WhatsAppPlatformFrameSerializer(FrameSerializer):
    """
    Binary PCM serializer for the WhatsApp Platform bridge.

    Wire format (same as the bridge sends/expects):
      - Sample rate:    16,000 Hz
      - Bit depth:      16-bit signed integer
      - Byte order:     Little-endian
      - Channels:       Mono (1)
      - Framing:        None — each WebSocket message is a raw PCM chunk

    Dograh reads: binary WebSocket message → Int16LE samples → AudioRawFrame
    Dograh writes: AudioRawFrame samples → Int16LE bytes → binary WebSocket message
    """

    SAMPLE_RATE   = 16000
    NUM_CHANNELS  = 1
    BYTES_PER_SAMPLE = 2  # 16-bit

    async def serialize(self, frame: Frame) -> bytes | None:
        """Convert an AudioRawFrame into raw PCM bytes to send to the bridge."""
        if not isinstance(frame, (AudioRawFrame, InputAudioRawFrame, OutputAudioRawFrame)):
            return None
        # frame.audio is already bytes of Int16LE PCM from TTS
        return frame.audio

    async def deserialize(self, data: bytes) -> Frame | None:
        """Convert raw PCM bytes from the bridge into an InputAudioRawFrame."""
        if not data:
            return None
        try:
            return InputAudioRawFrame(
                audio=data,
                sample_rate=self.SAMPLE_RATE,
                num_channels=self.NUM_CHANNELS,
            )
        except Exception as exc:
            logger.warning(f"[WhatsAppPlatform] Deserialize error: {exc}")
            return None
