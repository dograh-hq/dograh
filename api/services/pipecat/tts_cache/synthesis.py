"""Capture and replay for adapters with complete, independent PCM requests."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing

from loguru import logger

from api.services.observability.metrics import get_runtime
from api.services.pipecat.tts_cache.cache import SpeechCache
from api.services.pipecat.tts_cache.models import CachedSpeech, SynthesisRequest
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.tts_service import TTSService


def emits_only_audio(service: TTSService) -> bool:
    """Whether yielded audio frames fully describe a service's synthesis output.

    Word timestamps are pushed straight to the pipeline rather than yielded from
    run_tts, so the frame inspection below never observes them and a replayed
    entry would silently drop them. Token streaming splits one synthesis across
    several calls, leaving no request that is independently replayable. A service
    that does not report either property is treated as unsafe.
    """
    return (
        getattr(service, "_push_text_frames", False) is True
        and getattr(service, "_is_streaming_tokens", True) is False
    )


async def cached_synthesis(
    *,
    service: TTSService,
    cache: SpeechCache,
    request: SynthesisRequest,
    context_id: str,
    source: Callable[[], AsyncGenerator[Frame, None]],
    completed: Callable[[], bool],
    chunk_size: int,
    text_length: int,
) -> AsyncGenerator[Frame, None]:
    if not emits_only_audio(service):
        # An adapter reached the shared helper without meeting its output
        # contract. Caching here would drop pipeline frames on every hit, so the
        # call proceeds uncached rather than replaying an incomplete result.
        logger.error(
            "TTS cache refused {}: output is not fully described by audio frames",
            type(service).__name__,
        )
        cache.record(request.provider, "unsupported_service")
        async with aclosing(source()) as frames:
            async for frame in frames:
                yield frame
        return

    speech = await cache.get(request)
    if speech is not None:
        if runtime := get_runtime():
            attrs = {"provider": request.provider}
            runtime.tts_cache_avoided_characters.add(text_length, attrs)
            runtime.tts_cache_replay_seconds.record(
                len(speech.audio) / (speech.sample_rate * speech.channels * 2), attrs
            )
        alignment = 2 * speech.channels
        size = max(alignment, chunk_size // alignment * alignment)
        for offset in range(0, len(speech.audio), size):
            await asyncio.sleep(0)
            yield TTSAudioRawFrame(
                audio=speech.audio[offset : offset + size],
                sample_rate=speech.sample_rate,
                num_channels=speech.channels,
                context_id=context_id,
            )
        return

    audio = bytearray()
    token = None
    capture = False
    limit = cache.policy.audio_limit(request.sample_rate, request.channels)
    try:
        token = await cache.claim_candidate(request)
        capture = token is not None
        async with aclosing(source()) as frames:
            async for frame in frames:
                if isinstance(frame, TTSAudioRawFrame) and capture:
                    if (
                        frame.sample_rate != request.sample_rate
                        or frame.num_channels != request.channels
                        or len(frame.audio) % (2 * request.channels)
                        or len(audio) + len(frame.audio) > limit
                    ):
                        capture = False
                        cache.record(request.provider, "unsupported_or_oversized")
                    elif cache.reserve(len(frame.audio)):
                        audio.extend(frame.audio)
                    else:
                        capture = False
                        cache.record(request.provider, "capture_budget")
                elif isinstance(frame, ErrorFrame):
                    capture = False
                elif not isinstance(frame, TTSAudioRawFrame):
                    # Additional output (e.g. word timestamps) needs its own
                    # replay contract before an adapter can use this helper.
                    capture = False
                    cache.record(request.provider, "unsupported_frame")
                if not capture:
                    if audio:
                        cache.release(len(audio))
                        audio.clear()
                    if token is not None:
                        await cache.release_candidate(request, token)
                        token = None
                yield frame
        if capture and audio and token is not None and completed():
            await cache.put(
                request,
                CachedSpeech(bytes(audio), request.sample_rate, request.channels),
                token=token,
            )
        elif token is not None:
            cache.record(request.provider, "incomplete")
    finally:
        cache.release(len(audio))
        if token is not None:
            await cache.release_candidate(request, token)
