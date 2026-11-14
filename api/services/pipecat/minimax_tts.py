"""MiniMax TTS services backed by the speech cache.

MiniMaxOwnedSessionTTSService closes the aiohttp session Pipecat's
MiniMaxHttpTTSService leaves to the caller. MiniMaxCachingTTSService caches its
independent HTTP requests. MiniMaxCachingWebsocketTTSService caches the
sentences of the bidirectional WebSocket service, which is what the factory
builds.
"""

from contextlib import aclosing

import aiohttp

from api.services.observability.metrics import get_runtime
from api.services.pipecat.tts_cache.cache import SpeechCache
from api.services.pipecat.tts_cache.models import CachedSpeech, SynthesisRequest
from api.services.pipecat.tts_cache.synthesis import cached_synthesis
from pipecat.services.minimax.tts import (
    MiniMaxHttpTTSService,
    MiniMaxSentenceAudio,
    MiniMaxSynthesisOutcome,
    MiniMaxTTSService,
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


class MiniMaxCachingWebsocketTTSService(MiniMaxTTSService):
    """Play repeated sentences from the speech cache instead of synthesizing them.

    The WebSocket service decides when a cached sentence can play without
    reordering a reply, and which of MiniMax's audio belongs to which sentence.
    This class looks sentences up, reserves a contributor slot for a missed one,
    and stores its complete audio and word timestamps. As over HTTP, an entry is
    published once three takes are in, choosing the median-length one.
    """

    # Bumped when the stored audio or the identity below changes meaning.
    # Version 1 is the HTTP adapter, whose requests are keyed differently anyway.
    ADAPTER_VERSION = 2

    def __init__(self, *, speech_cache: SpeechCache, organization_id: int, **kwargs):
        super().__init__(**kwargs)
        self._speech_cache = speech_cache
        self._organization_id = organization_id
        self._sentence_cache_enabled = True

    def _request(self, text: str) -> SynthesisRequest | None:
        """The cache identity of ``text``: the session's task_start fields plus the text."""
        payload = {
            key: value
            for key, value in self._task_start_message().items()
            if key != "event"
        }
        # Whitespace between texts is the service's own separator, not speech.
        payload["text"] = " ".join(text.split())
        try:
            return SynthesisRequest.from_payload(
                organization_id=self._organization_id,
                provider="minimax",
                adapter_version=self.ADAPTER_VERSION,
                endpoint=self._url,
                credential=self._api_key,
                payload=payload,
                sample_rate=self.sample_rate,
                channels=1,
            )
        except (ValueError, TypeError, KeyError):
            return None

    async def _cached_sentence(self, text: str) -> MiniMaxSentenceAudio | None:
        request = self._request(text)
        if request is None:
            self._speech_cache.record("minimax", "ineligible")
            return None
        speech = await self._speech_cache.get(request)
        if speech is None:
            return None
        if runtime := get_runtime():
            attrs = {"provider": "minimax"}
            runtime.tts_cache_avoided_characters.add(len(text.strip()), attrs)
            runtime.tts_cache_replay_seconds.record(speech.duration_seconds, attrs)
        return MiniMaxSentenceAudio(audio=speech.audio, words=speech.words)

    async def _reserve_sentence_capture(self, text: str):
        request = self._request(text)
        if request is None:
            return None
        token = await self._speech_cache.claim_candidate(request)
        return (request, token) if token is not None else None

    async def _sentence_captured(
        self, text: str, token, sentence: MiniMaxSentenceAudio
    ):
        request, claim = token
        speech = CachedSpeech(
            sentence.audio, request.sample_rate, request.channels, sentence.words
        )
        try:
            speech.validate(request, self._speech_cache.policy)
        except ValueError:
            # Too long, or timestamps that do not fit the audio: not a cacheable take.
            self._speech_cache.record("minimax", "unsupported_or_oversized")
        else:
            await self._speech_cache.put(request, speech, token=claim)
        finally:
            await self._speech_cache.release_candidate(request, claim)

    async def _sentence_capture_dropped(self, text: str, token):
        request, claim = token
        self._speech_cache.record("minimax", "incomplete")
        # A slot that is never released expires with its reservation anyway.
        await self._speech_cache.release_candidate(request, claim)
