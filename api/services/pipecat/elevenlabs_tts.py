"""ElevenLabs HTTP TTS wrapper for models the TTS WebSockets do not serve.

ElevenLabs' text-to-speech WebSockets do not support the Eleven v3 or Eleven v4
models; those stream over the HTTP API instead. Pipecat's
ElevenLabsHttpTTSService leaves session disposal to the caller. Our factory
creates a fresh session per service instance, so we own its close here to
avoid leaking sockets/FDs on shutdown.
"""

import aiohttp

from pipecat.services.elevenlabs.tts import ElevenLabsHttpTTSService

# Model id prefixes ElevenLabs serves over HTTP streaming but not WebSockets.
HTTP_ONLY_MODEL_PREFIXES = ("eleven_v3", "eleven_v4")


def requires_http_streaming(model: str | None) -> bool:
    """Return True when ``model`` cannot use the ElevenLabs TTS WebSockets."""
    return bool(model) and model.startswith(HTTP_ONLY_MODEL_PREFIXES)


class ElevenLabsOwnedSessionHttpTTSService(ElevenLabsHttpTTSService):
    """ElevenLabsHttpTTSService variant that owns its aiohttp session lifecycle."""

    def __init__(self, *args, aiohttp_session: aiohttp.ClientSession, **kwargs):
        super().__init__(*args, aiohttp_session=aiohttp_session, **kwargs)
        self._owned_session = aiohttp_session

    async def cleanup(self):
        try:
            await super().cleanup()
        finally:
            if not self._owned_session.closed:
                await self._owned_session.close()
