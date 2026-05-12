"""Exotel frame serializer.

Exotel streams audio over WebSocket using the same JSON envelope and μ-law
8 kHz encoding as Plivo. We re-export PlivoFrameSerializer directly so
transport.py can import from `.serializers` and we have an obvious place to
drop a custom subclass later if Exotel diverges.
"""

from pipecat.serializers.plivo import PlivoFrameSerializer as ExotelFrameSerializer

__all__ = ["ExotelFrameSerializer"]
