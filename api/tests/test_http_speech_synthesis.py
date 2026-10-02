"""Exercise HTTP adapters through the shared synthesis pipeline without provider calls."""

import base64
import io
import json
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from pipecat.services.cartesia.tts import CartesiaHttpTTSService, CartesiaTTSSettings
from pipecat.services.deepgram.tts import DeepgramHttpTTSService, DeepgramTTSSettings
from pipecat.services.elevenlabs.tts import (
    ElevenLabsHttpTTSService,
    ElevenLabsHttpTTSSettings,
)
from pipecat.services.minimax.tts import MiniMaxTTSSettings
from pipecat.services.openai.tts import OpenAITTSService, OpenAITTSSettings
from pipecat.services.speechify.tts import SpeechifyTTSSettings

from api.services.pipecat import speech_synthesis
from api.services.pipecat.minimax_tts import MiniMaxOwnedSessionTTSService
from api.services.pipecat.speechify_tts import SpeechifyOwnedSessionTTSService

PCM = b"\x01\x00" * 800


class Response:
    def __init__(self, chunks, status=200):
        self.chunks = chunks
        self.status = self.status_code = status
        self.headers = {}
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def read(self):
        return b"".join(self.chunks)

    async def text(self):
        return "provider error"

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=Mock(real_url="https://example.com/synthesize"),
                history=(),
                status=self.status,
                message="provider error",
            )

    async def iter_chunked(self, _size=None):
        for chunk in self.chunks:
            yield chunk

    iter_any = iter_chunked
    iter_bytes = iter_chunked

    async def __aiter__(self):
        for line in b"".join(self.chunks).splitlines(keepends=True):
            yield line


class Session:
    def __init__(self, response):
        self.response = response
        self.posts = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        self.closed = True

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.response


def make_tts(provider, session):
    adapters = {
        "cartesia": (CartesiaHttpTTSService, CartesiaTTSSettings),
        "deepgram": (DeepgramHttpTTSService, DeepgramTTSSettings),
        "elevenlabs": (ElevenLabsHttpTTSService, ElevenLabsHttpTTSSettings),
        "minimax": (MiniMaxOwnedSessionTTSService, MiniMaxTTSSettings),
        "speechify": (SpeechifyOwnedSessionTTSService, SpeechifyTTSSettings),
    }
    if provider == "openai":
        return OpenAITTSService(
            api_key="test-key", settings=OpenAITTSSettings(voice="alloy")
        )
    service, settings = adapters[provider]
    kwargs = {}
    if provider == "minimax":
        kwargs = {"group_id": "test-group"}
    return service(
        api_key="test-key",
        aiohttp_session=session,
        settings=settings(voice="test-voice"),
        **kwargs,
    )


def response_for(provider):
    encoded = base64.b64encode(PCM).decode()
    if provider == "elevenlabs":
        return Response(
            [
                json.dumps(
                    {
                        "audio_base64": encoded,
                        "alignment": {
                            "characters": list("Hello."),
                            "character_start_times_seconds": [0] * 6,
                            "character_end_times_seconds": [0.1] * 6,
                        },
                    }
                ).encode()
                + b"\n"
            ]
        )
    if provider == "minimax":
        return Response(
            [
                (
                    "data: "
                    + json.dumps({"data": {"status": 1, "audio": PCM.hex()}})
                    + "\n\n"
                ).encode(),
                b'data: {"data":{"status":2,"audio":""}}\n\n',
            ]
        )
    if provider == "speechify":
        return Response(
            [
                (
                    "event: speech.chunk\ndata: "
                    + json.dumps({"audio": encoded})
                    + "\n\n"
                ).encode(),
                b'event: speech.done\ndata: {"audio_duration_ms":100}\n\n',
            ]
        )
    # HTTP chunks can split an individual PCM sample.
    return Response([PCM[:501], PCM[501:]])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    ["cartesia", "deepgram", "elevenlabs", "openai", "minimax", "speechify"],
)
async def test_http_adapter_returns_complete_wav_and_closes_connections(
    monkeypatch, provider
):
    session = Session(response_for(provider))
    sdk_close = AsyncMock()
    sdk_create = Mock(return_value=response_for("openai"))
    if provider == "openai":
        monkeypatch.setattr(
            "pipecat.services.openai.tts.AsyncOpenAI",
            lambda **kwargs: SimpleNamespace(
                audio=SimpleNamespace(
                    speech=SimpleNamespace(
                        with_streaming_response=SimpleNamespace(create=sdk_create)
                    )
                ),
                close=sdk_close,
            ),
        )
    tts = make_tts(provider, session)
    async with session:
        audio = await speech_synthesis.synthesize_speech(
            tts, "Hello.", sample_rate=24000, timeout=2
        )
    with wave.open(io.BytesIO(audio)) as wav:
        assert wav.getframerate() == 24000
        assert wav.getnchannels() == 1
        assert wav.readframes(wav.getnframes()) == PCM
    assert session.closed
    if provider == "openai":
        sdk_close.assert_awaited_once()
        assert sdk_create.call_args.kwargs["input"] == "Hello."
    else:
        url, request = session.posts[0]
        assert url.startswith("https://")
        if provider == "cartesia":
            assert request["json"]["voice"]["id"] == "test-voice"
        elif provider == "elevenlabs":
            assert request["params"]["output_format"] == "pcm_24000"


@pytest.mark.asyncio
async def test_midstream_speechify_error_does_not_return_partial_recording():
    response = response_for("speechify")
    response.chunks[-1] = (
        b'event: speech.error\ndata: {"error":{"code":"invalid_request","message":"failed"}}\n\n'
    )
    async with Session(response) as session:
        with pytest.raises(ValueError):
            await speech_synthesis.synthesize_speech(
                make_tts("speechify", session), "Hello.", timeout=2
            )
