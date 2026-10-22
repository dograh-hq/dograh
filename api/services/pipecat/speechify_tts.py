"""Speechify TTS wrapper that closes its aiohttp session in cleanup().

Pipecat's SpeechifyHttpTTSService leaves session disposal to the caller. Our
factory creates a fresh session per service instance, so we own its close
here to avoid leaking sockets/FDs on shutdown.

It also replaces pipecat's caller attribution headers with Dograh's, so
Speechify credits the traffic to Dograh rather than to pipecat.
"""

import aiohttp

from api.constants import SPEECHIFY_CALLER_HEADERS
from pipecat.services.speechify.tts import SpeechifyHttpTTSService


class SpeechifyOwnedSessionTTSService(SpeechifyHttpTTSService):
    """SpeechifyHttpTTSService variant that owns its aiohttp session lifecycle."""

    def __init__(self, *args, aiohttp_session: aiohttp.ClientSession, **kwargs):
        super().__init__(*args, aiohttp_session=aiohttp_session, **kwargs)
        self._owned_session = aiohttp_session
        self._headers.update(SPEECHIFY_CALLER_HEADERS)

    async def cleanup(self):
        try:
            await super().cleanup()
        finally:
            if not self._owned_session.closed:
                await self._owned_session.close()
