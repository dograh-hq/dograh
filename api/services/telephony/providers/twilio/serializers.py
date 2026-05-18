"""Twilio frame serializer.

Re-exported from pipecat. Kept local so transport.py imports from
``.serializers`` and we have an obvious place to drop a custom subclass if
pipecat upstream lags.
"""

from pipecat.serializers.twilio import TwilioFrameSerializer

__all__ = ["TwilioFrameSerializer"]
