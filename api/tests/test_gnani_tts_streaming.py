"""Exercise Gnani's real WebSocket adapter and Pipecat's downstream audio queue."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    ErrorFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat_gnani import GnaniTTSService

from api.services.configuration.registry import GnaniTTSConfiguration
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import create_tts_service
from api.services.pipecat.worker_runner import (
    run_pipeline_worker,
    wait_for_pipeline_worker_started,
)


class FakeGnaniWebSocket:
    def __init__(self):
        self.requests = asyncio.Queue()
        self.messages = asyncio.Queue()
        self.closed = False

    async def send(self, message):
        self.requests.put_nowait(json.loads(message)["text"].strip())

    async def __aiter__(self):
        while True:
            yield await self.messages.get()

    async def close(self):
        self.closed = True

    def audio(self, pcm, *, complete=True):
        self.messages.put_nowait(pcm)
        if complete:
            self.messages.put_nowait('{"type":"complete"}')


@pytest.fixture(params=["timbre-v2.5", "timbre-v3.0"])
async def playback(request, monkeypatch):
    socket, replacement = FakeGnaniWebSocket(), FakeGnaniWebSocket()
    monkeypatch.setattr(
        "pipecat_gnani.tts.websocket_connect",
        AsyncMock(side_effect=[socket, replacement]),
    )
    config = GnaniTTSConfiguration(api_key="test-key", model=request.param)
    service = create_tts_service(SimpleNamespace(tts=config), AudioConfig(16000, 16000))
    worker = PipelineWorker(Pipeline([service]), enable_rtvi=False)
    worker.set_reached_downstream_filter(
        (TTSAudioRawFrame, TTSStoppedFrame, InterruptionFrame)
    )
    downstream = asyncio.Queue()

    @worker.event_handler("on_frame_reached_downstream")
    async def collect(_, frame):
        downstream.put_nowait(frame)

    async def reply(text):
        await worker.queue_frames(
            [LLMFullResponseStartFrame(), LLMTextFrame(text), LLMFullResponseEndFrame()]
        )

    runner = asyncio.create_task(run_pipeline_worker(worker))
    try:
        await wait_for_pipeline_worker_started(worker, run_task=runner)
        yield SimpleNamespace(
            worker=worker,
            reply=reply,
            socket=socket,
            replacement=replacement,
            downstream=downstream,
            service=service,
        )
    finally:
        await worker.cancel()
        await asyncio.wait_for(runner, 5)


async def test_two_sentences_play_in_one_reply(playback):
    async with asyncio.timeout(5):
        await playback.reply("First sentence. Second sentence.")
        sentences = [
            ("First sentence.", b"\x01\x00" * 160),
            ("Second sentence.", b"\x02\x00" * 160),
        ]
        # Respond as soon as each request arrives, independently of playback.
        for text, pcm in sentences:
            assert await playback.socket.requests.get() == text
            playback.socket.audio(pcm)
        contexts = set()
        for _, pcm in sentences:
            audio = await playback.downstream.get()
            assert isinstance(audio, TTSAudioRawFrame)
            contexts.add(audio.context_id)
            assert audio.audio == pcm
            stop = await playback.downstream.get()
            assert isinstance(stop, TTSStoppedFrame)
            assert stop.context_id == audio.context_id
        assert len(contexts) == 2


async def test_interruption_discards_old_audio_and_next_reply_plays(playback):
    prefix, late, fresh = (bytes([value, 0]) * 160 for value in (1, 2, 3))
    async with asyncio.timeout(5):
        await playback.reply("Interrupted reply. Queued sentence.")
        assert await playback.socket.requests.get() == "Interrupted reply."
        playback.socket.audio(prefix, complete=False)
        assert (await playback.downstream.get()).audio == prefix
        await playback.worker.queue_frame(InterruptionFrame())
        assert isinstance(await playback.downstream.get(), InterruptionFrame)
        assert playback.socket.closed
        assert playback.socket.requests.empty()

        await playback.reply("New reply.")
        assert await playback.replacement.requests.get() == "New reply."
        # Late data from the interrupted request must never play in the new context.
        playback.socket.audio(late)
        playback.replacement.audio(fresh)
        assert (await playback.downstream.get()).audio == fresh
        assert isinstance(await playback.downstream.get(), TTSStoppedFrame)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(playback.downstream.get(), 0.1)


async def test_missing_completion_does_not_block_the_next_sentence(playback):
    playback.service._stop_frame_timeout_s = 0.2
    async with asyncio.timeout(5):
        await playback.reply("Stalled sentence. Next sentence.")
        assert await playback.socket.requests.get() == "Stalled sentence."
        # Gnani never completes the first request. Pipecat expires its context.
        assert isinstance(await playback.downstream.get(), TTSStoppedFrame)
        assert await playback.replacement.requests.get() == "Next sentence."
        assert playback.socket.closed
        pcm = b"\x04\x00" * 160
        playback.socket.audio(b"\x05\x00" * 160)
        playback.replacement.audio(pcm)
        assert (await playback.downstream.get()).audio == pcm
        assert isinstance(await playback.downstream.get(), TTSStoppedFrame)


@pytest.mark.parametrize("failure", ["raise", "close", "error_frame"])
async def test_abandoned_send_allows_next_request(playback, monkeypatch, failure):
    async def fail_after_send(service, text, context_id):
        if failure == "error_frame":
            yield ErrorFrame(error="send failed")
            return
        yield None
        raise RuntimeError("send failed")

    service = playback.service
    with monkeypatch.context() as patch:
        patch.setattr(GnaniTTSService, "run_tts", fail_after_send)
        request = service.run_tts("First.", "first")
        await anext(request)
        if failure == "close":
            await request.aclose()
        elif failure == "error_frame":
            with pytest.raises(StopAsyncIteration):
                await anext(request)
        else:
            with pytest.raises(RuntimeError, match="send failed"):
                await anext(request)

    assert playback.socket.closed
    async with asyncio.timeout(2):
        await playback.reply("Next reply.")
        assert await playback.replacement.requests.get() == "Next reply."


async def test_closing_completed_sender_preserves_new_request(playback):
    service = playback.service
    first = service.run_tts("First.", "first")
    await anext(first)
    playback.socket.audio(b"")
    async with asyncio.timeout(2):
        second = service.run_tts("Second.", "second")
        await anext(second)
        await first.aclose()
        assert service.get_active_audio_context_id() == "second"
        assert not playback.socket.closed
        await second.aclose()
