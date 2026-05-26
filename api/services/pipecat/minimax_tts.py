"""MiniMax TTS wrapper that closes its aiohttp session in cleanup().

Pipecat's MiniMaxHttpTTSService leaves session disposal to the caller. Our
factory creates a fresh session per service instance, so we own its close
here to avoid leaking sockets/FDs on shutdown.
"""

import aiohttp

from pipecat.services.minimax.tts import MiniMaxHttpTTSService


class MiniMaxOwnedSessionTTSService(MiniMaxHttpTTSService):
    """MiniMaxHttpTTSService variant that owns its aiohttp session lifecycle."""

    def __init__(self, *args, aiohttp_session: aiohttp.ClientSession, **kwargs):
        super().__init__(*args, aiohttp_session=aiohttp_session, **kwargs)
        self._owned_session = aiohttp_session

    async def cleanup(self):
        await super().cleanup()
        if not self._owned_session.closed:
            await self._owned_session.close()
