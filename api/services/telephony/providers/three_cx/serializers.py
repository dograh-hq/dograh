"""Asterisk frame serializer (re-exported from pipecat).

3CX runs through an Asterisk bridge, so the wire format and serializer are
identical to the ARI provider. We re-export rather than import from
``..ari`` to keep providers/__init__ from accidentally creating cross-package
coupling — see providers/AGENTS.md.
"""

from pipecat.serializers.asterisk import AsteriskFrameSerializer

__all__ = ["AsteriskFrameSerializer"]
