"""MiniMax TTS wrapper that closes its aiohttp session in cleanup().

Pipecat's MiniMaxHttpTTSService leaves session disposal to the caller. Our
factory creates a fresh session per service instance, so we own its close
here to avoid leaking sockets/FDs on shutdown.
"""

from contextlib import aclosing

import aiohttp

from api.services.pipecat.tts_cache.cache import SpeechCache
from api.services.pipecat.tts_cache.models import SynthesisRequest
from api.services.pipecat.tts_cache.synthesis import cached_synthesis
from pipecat.services.minimax.tts import (
    MiniMaxHttpTTSService,
    MiniMaxSynthesisOutcome,
)
from pipecat.utils.tracing.service_decorators import traced_tts


class MiniMaxOwnedSessionTTSService(MiniMaxHttpTTSService):
    """MiniMaxHttpTTSService variant that owns its aiohttp session lifecycle."""

    def __init__(self, *args, aiohttp_session: aiohttp.ClientSession, **kwargs):
        super().__init__(*args, aiohttp_session=aiohttp_session, **kwargs)
        self._owned_session = aiohttp_session

    async def cleanup(self):
        try:
            await super().cleanup()
        finally:
            if not self._owned_session.closed:
                await self._owned_session.close()


class MiniMaxCachingTTSService(MiniMaxOwnedSessionTTSService):
    """Cache validated independent MiniMax PCM requests within an organization."""

    def __init__(self, *, speech_cache: SpeechCache, organization_id: int, **kwargs):
        super().__init__(**kwargs)
        self._speech_cache = speech_cache
        self._organization_id = organization_id

    @traced_tts
    async def run_tts(self, text: str, context_id: str):
        payload = self._build_request(text)
        outcome = MiniMaxSynthesisOutcome()

        def source():
            return self._run_tts_request(payload, context_id, outcome)

        request = None
        if payload["stream"] and payload["audio_setting"]["format"] == "pcm":
            try:
                request = SynthesisRequest.from_payload(
                    organization_id=self._organization_id,
                    provider="minimax",
                    adapter_version=1,
                    endpoint=self._base_url,
                    credential=self._api_key,
                    payload=payload,
                    sample_rate=payload["audio_setting"]["sample_rate"],
                    channels=payload["audio_setting"]["channel"],
                )
            except (ValueError, TypeError, KeyError):
                pass
        if request is None:
            self._speech_cache.record("minimax", "ineligible")
            frames = source()
        else:
            frames = cached_synthesis(
                service=self,
                cache=self._speech_cache,
                request=request,
                context_id=context_id,
                source=source,
                completed=lambda: outcome.completed,
                chunk_size=self.chunk_size,
                text_length=len(text),
            )
        async with aclosing(frames):
            async for frame in frames:
                yield frame
