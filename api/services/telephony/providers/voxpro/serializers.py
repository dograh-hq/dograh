"""VoxPro frame serializer.

VoxPro's connector emits the Plivo/Twilio-standard telephony WebSocket protocol
(8 kHz mu-law, base64 in JSON), so we reuse the published Plivo serializer
directly — no VoxPro-specific pipecat module is required.
"""

from pipecat.serializers.plivo import PlivoFrameSerializer as VoxProFrameSerializer

__all__ = ["VoxProFrameSerializer"]
