"""The speech cache behind the WebSocket MiniMax service.

Each call gets its own service, as in production, sharing one cache. MiniMax's
side of the session is played by feeding its events in, in the order the live
bidirectional API sends them.
"""

import json

import pytest
from pipecat.frames.frames import TTSAudioRawFrame, TTSStoppedFrame
from websockets.protocol import State

from api.services.pipecat.minimax_tts import MiniMaxCachingWebsocketTTSService
from api.services.pipecat.tts_cache.cache import SpeechCache
from api.services.pipecat.tts_cache.models import CachePolicy
from api.tests.test_tts_cache import MemoryBackend

pytestmark = pytest.mark.asyncio

TEXT = "Buongiorno, sono Filippo di Acme. "
OK = {"status_code": 0, "status_msg": "success"}


class _FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self.state = State.OPEN

    async def send(self, raw: str):
        self.sent.append(json.loads(raw))

    async def close(self):
        self.state = State.CLOSED


def _event(event: str, trace: str = "session", **extra) -> dict:
    return {
        "event": event,
        "trace_id": trace,
        "session_id": "s",
        "base_resp": OK,
        **extra,
    }


def _subtitle(text: str) -> dict:
    """One subtitle piece per word, 0.3 s apart."""
    pieces, at = [], 0
    for i, word in enumerate(text.split()):
        begin = text.index(word, at)
        at = begin + len(word)
        pieces.append(
            {
                "word": word,
                "word_begin": begin,
                "word_end": at,
                "time_begin": 300 * i,
                "time_end": 300 * i + 250,
            }
        )
    return {"text": text, "timestamped_words": pieces}


def _sentence(text: str, n_bytes: int) -> list[dict]:
    return [
        _event("sentence_start"),
        _event(
            "task_continued",
            "t",
            data={"audio": "01" * n_bytes, "subtitle": _subtitle(text)},
            is_final=False,
        ),
        _event("task_continued", "t", data={"audio": ""}, is_final=True),
        _event("sentence_end", "t"),
    ]


@pytest.fixture
def cache():
    return SpeechCache(MemoryBackend(), CachePolicy())


def _service(monkeypatch, cache, **settings) -> MiniMaxCachingWebsocketTTSService:
    service = MiniMaxCachingWebsocketTTSService(
        speech_cache=cache,
        organization_id=7,
        api_key="test-key",
        sample_rate=8000,
        settings=MiniMaxCachingWebsocketTTSService.Settings(
            model="speech-2.8-turbo", voice="Italian_Voice", **settings
        ),
    )
    service._sample_rate = 8000
    service._websocket = _FakeSocket()
    service.audio, service.words, service.stopped = [], [], []

    async def append_to_audio_context(context_id, frame):
        if isinstance(frame, TTSAudioRawFrame):
            service.audio.append(len(frame.audio))
        elif isinstance(frame, TTSStoppedFrame):
            service.stopped.append(context_id)

    async def add_word_timestamps(word_times, context_id=None, **_):
        service.words.extend((word, round(t, 3)) for word, t in word_times)

    async def nothing(*_args, **_kwargs):
        pass

    monkeypatch.setattr(service, "append_to_audio_context", append_to_audio_context)
    monkeypatch.setattr(service, "add_word_timestamps", add_word_timestamps)
    monkeypatch.setattr(service, "remove_audio_context", nothing)
    monkeypatch.setattr(service, "push_error_frame", nothing)
    monkeypatch.setattr(service, "audio_context_available", lambda _context_id: True)
    monkeypatch.setattr(service, "start_tts_usage_metrics", nothing)
    monkeypatch.setattr(service, "stop_all_metrics", nothing)
    return service


async def _call(service, text: str, n_bytes: int | None = None):
    """Speak one reply; if MiniMax is asked for it, answer with ``n_bytes`` of audio."""
    async for _ in service.run_tts(text, "reply"):
        pass
    await service.flush_audio("reply")
    if service._websocket.sent:
        for message in [*_sentence(text.strip(), n_bytes), _event("task_flushed")]:
            await service._on_message(message)


def _events(service) -> list[str]:
    return [message["event"] for message in service._websocket.sent]


async def test_the_median_of_three_takes_replays_on_the_fourth_call(monkeypatch, cache):
    # 1.875 s, 1.5 s and 1.6875 s of 8 kHz audio; the words run to 1.45 s.
    for n_bytes in (30000, 24000, 27000):
        take = _service(monkeypatch, cache)
        await _call(take, TEXT, n_bytes)
        assert _events(take) == ["task_continue", "task_flush"]

    replay = _service(monkeypatch, cache)
    await _call(replay, TEXT)

    assert _events(replay) == []
    assert sum(replay.audio) == 27000
    assert replay.words == [
        ("Buongiorno,", 0.08),
        ("sono", 0.38),
        ("Filippo", 0.68),
        ("di", 0.98),
        ("Acme.", 1.28),
    ]
    assert replay.stopped == ["reply"]


async def test_a_take_cut_off_by_an_interruption_is_not_collected(monkeypatch, cache):
    service = _service(monkeypatch, cache)
    async for _ in service.run_tts(TEXT, "reply"):
        pass
    await service._on_message(_event("sentence_start"))

    await service._cancel_outstanding()

    backend = cache.backend
    assert not any(backend.reservations.values())
    assert not backend.candidates


@pytest.mark.parametrize(
    "changed",
    [
        {"pronunciation_dict": ["Acme/(ˈækmi)"]},
        {"text_normalization_mode": "basic"},
        {"speed": 1.2},
        {"language_boost": "Italian"},
    ],
)
async def test_every_task_start_setting_is_part_of_the_identity(
    monkeypatch, cache, changed
):
    plain = _service(monkeypatch, cache)._request(TEXT)
    other = _service(monkeypatch, cache, **changed)._request(TEXT)

    assert plain.digest != other.digest


async def test_whitespace_around_a_text_does_not_change_its_identity(
    monkeypatch, cache
):
    service = _service(monkeypatch, cache)

    assert (
        service._request(TEXT).digest == service._request(f"  {TEXT.strip()}\n").digest
    )
    assert service._request(TEXT).digest != service._request("Buonasera.").digest


async def test_identity_is_separate_from_the_http_adapter(monkeypatch, cache):
    request = _service(monkeypatch, cache)._request(TEXT)

    assert MiniMaxCachingWebsocketTTSService.ADAPTER_VERSION == 2
    assert request.provider == "minimax"
    assert request.organization_id == 7
    assert request.text_preview == TEXT.strip()
