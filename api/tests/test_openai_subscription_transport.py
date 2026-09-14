import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import aiohttp
import aiortc
import av
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from api.services.pipecat.realtime.openai_live_subscription_transport import (
    FRAME_SAMPLES,
    MAX_APPEND_BYTES,
    MAX_PENDING_PCM_BYTES,
    MAX_SDP_BYTES,
    PCM_RATE,
    SUBSCRIPTION_CALL_URL,
    OpenAILiveSubscriptionTransport,
    SubscriptionTransportError,
    build_subscription_session,
    chunk_append_text,
    context_append_events,
    delegation_context_events,
    subscription_call_id,
    validate_sdp,
)

SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
TOKEN = "synthetic-oauth-token-for-offline-tests"
ACCOUNT = "synthetic-account-for-offline-tests"
SESSION = build_subscription_session(
    model="gpt-live-1-codex", voice="cove", instructions="Use the workflow backend."
)


class FakePeer:
    def __init__(self):
        self.handlers = {}
        self.connectionState = "new"
        self.closed = False
        self.localDescription = None

    def on(self, event):
        def register(handler):
            self.handlers[event] = handler
            return handler

        return register

    def addTrack(self, track):
        self.track = track

    async def createOffer(self):
        return SimpleNamespace(sdp=SDP, type="offer")

    async def setLocalDescription(self, offer):
        self.localDescription = offer

    async def setRemoteDescription(self, answer):
        self.answer = answer
        self.connectionState = "connected"
        await self.handlers["connectionstatechange"]()

    async def close(self):
        self.closed = True
        self.connectionState = "closed"


class FakeHTTP:
    def __init__(self):
        self.status = 201
        self.body = SDP.encode()
        self.headers = {"openai-session-id": "rtc_test_call"}
        self.calls = []
        self.failure = None

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.failure:
            raise self.failure

        async def chunks():
            yield self.body

        yield SimpleNamespace(
            status_code=self.status, headers=self.headers, aiter_bytes=chunks
        )


class FakeWebSocket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.failure = None

    async def receive(self):
        return await self.queue.get()

    async def send_json(self, event):
        if self.failure:
            raise self.failure
        self.sent.append(event)

    async def emit(self, event):
        await self.queue.put(SimpleNamespace(type="text", data=json.dumps(event)))


class FakeAiohttp:
    TraceConfig = aiohttp.TraceConfig

    WSMsgType = SimpleNamespace(
        TEXT="text",
        BINARY="binary",
        CLOSE="close",
        CLOSED="closed",
        CLOSING="closing",
        ERROR="error",
    )

    def __init__(self):
        self.socket = FakeWebSocket()
        self.calls = []
        self.closed = False
        self.failure = None

    @asynccontextmanager
    async def ClientSession(self, *, trace_configs):
        self.trace_configs = trace_configs
        try:
            yield self
        finally:
            self.closed = True

    @asynccontextmanager
    async def ws_connect(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.failure:
            raise self.failure
        try:
            yield self.socket
        finally:
            self.socket.closed = True


@pytest.fixture
def rig():
    peer, http, aio = FakePeer(), FakeHTTP(), FakeAiohttp()
    events, audio = [], []

    async def on_event(event):
        events.append(event)

    async def on_audio(data, rate, channels):
        audio.append((data, rate, channels))

    rtc = SimpleNamespace(
        AudioStreamTrack=aiortc.AudioStreamTrack,
        RTCSessionDescription=aiortc.RTCSessionDescription,
        RTCPeerConnection=lambda: peer,
        mediastreams=aiortc.mediastreams,
    )
    transport = OpenAILiveSubscriptionTransport(
        on_event=on_event,
        on_audio=on_audio,
        http_client=http,
        aiohttp_module=aio,
        rtc_module=rtc,
        av_module=av,
        connect_timeout=0.1,
    )
    return SimpleNamespace(
        transport=transport,
        peer=peer,
        http=http,
        aio=aio,
        events=events,
        audio=audio,
    )


async def connect(rig):
    await rig.transport.connect(TOKEN, ACCOUNT, SESSION)


async def drain_until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.parametrize(
    "headers",
    [
        {"openai-session-id": "../../private"},
        {"location": "https://evil.example/v1/live/rtc_bad"},
        {"location": "https://api.openai.com@evil.example/v1/live/rtc_bad"},
        {"location": "https://api.openai.com:443/v1/live/rtc_bad"},
        {"location": "http://api.openai.com/v1/live/rtc_bad"},
        {"location": "https://api.openai.com/v1/live/rtc_bad?token=private"},
        {"location": "/v1/live/rtc_bad#private"},
        {"location": "/v1/live/rtc_one", "openai-session-id": "rtc_two"},
        {},
    ],
)
def test_rejects_untrusted_or_conflicting_call_identity(headers):
    with pytest.raises(SubscriptionTransportError):
        subscription_call_id(headers)


@pytest.mark.parametrize(
    "headers",
    [
        {"OpenAI-Session-ID": "rtc_test_call"},
        {"location": "/v1/live/rtc_test_call"},
        {
            "location": "https://chatgpt.com/backend-api/codex/realtime/calls/rtc_test_call"
        },
    ],
)
def test_accepts_bounded_trusted_call_identity(headers):
    assert subscription_call_id(headers) == "rtc_test_call"


@pytest.mark.parametrize(
    "sdp",
    ["", "v=1\nm=audio x", "v=0\nm=video x", SDP + "\0", SDP + "x" * MAX_SDP_BYTES],
    ids=["empty", "version", "video", "nul", "oversized"],
)
def test_rejects_invalid_sdp(sdp):
    with pytest.raises(SubscriptionTransportError):
        validate_sdp(sdp)


def test_subscription_session_has_only_client_delegation_and_webrtc_settings():
    assert SESSION == {
        "model": "gpt-live-1-codex",
        "instructions": "Use the workflow backend.",
        "audio": {"output": {"voice": "cove"}},
        "delegation": {"type": "client"},
    }
    with pytest.raises(SubscriptionTransportError):
        build_subscription_session(model="gpt-live-1", voice="cove", instructions="")


def test_delegation_unicode_chunking_preserves_identity_and_spoken_channel():
    text = "a\U0001f642\u754c" * 300
    chunks = chunk_append_text(text)
    assert "".join(chunks) == text
    assert all(len(chunk.encode()) <= MAX_APPEND_BYTES for chunk in chunks)
    spoken = delegation_context_events("delegation_123", text, spoken=True)
    assert "".join(event["content"][0]["text"] for event in spoken) == text
    assert all(event["type"] == "delegation.context.append" for event in spoken)
    assert all(event["delegation_item_id"] == "delegation_123" for event in spoken)
    assert all(event["channel"] == "speakable" for event in spoken)
    assert (
        delegation_context_events("delegation_123", "private thinking", spoken=False)[
            0
        ]["channel"]
        == "commentary"
    )
    assert context_append_events("Hello")[0] == {
        "type": "session.instructions.append",
        "content": "Hello",
        "delegation_id": None,
    }


@pytest.mark.asyncio
async def test_connect_uses_oauth_only_with_fixed_trusted_endpoints_and_no_redirects(
    rig,
):
    await connect(rig)
    method, url, request = rig.http.calls[0]
    assert method == "POST" and url == SUBSCRIPTION_CALL_URL
    assert request["follow_redirects"] is False
    assert request["json"] == {"sdp": SDP, "session": SESSION}
    assert request["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert request["headers"]["chatgpt-account-id"] == ACCOUNT
    assert request["headers"]["OpenAI-Alpha"] == "quicksilver=v2"
    sideband_url, sideband = rig.aio.calls[0]
    assert sideband_url == "wss://api.openai.com/v1/live/rtc_test_call"
    assert sideband["headers"] == request["headers"]
    assert rig.peer.answer.sdp == SDP
    assert rig.transport.session_id == "rtc_test_call"
    await rig.transport.close()
    assert rig.peer.closed and rig.aio.socket.closed and rig.aio.closed
    assert rig.peer.track.readyState == "ended"
    assert not rig.transport._tasks


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_error"),
        (403, "authentication_error"),
        (429, "rate_limited"),
        (302, "negotiation_failed"),
        (500, "negotiation_failed"),
    ],
)
@pytest.mark.asyncio
async def test_provider_http_failures_are_redacted_and_close_media(rig, status, code):
    rig.http.status, rig.http.body = status, TOKEN.encode()
    with pytest.raises(SubscriptionTransportError) as caught:
        await connect(rig)
    assert caught.value.code == code
    assert TOKEN not in str(caught.value) and ACCOUNT not in str(caught.value)
    assert rig.peer.closed and rig.transport.closed
    assert not rig.aio.calls


@pytest.mark.parametrize(
    "body",
    [
        b"not SDP",
        SDP.encode() + b"x" * MAX_SDP_BYTES,
        (SDP + TOKEN).encode(),
        (SDP + ACCOUNT).encode(),
        SDP.encode() + b"\xff",
    ],
    ids=["invalid", "oversized", "reflected-token", "reflected-account", "non-utf8"],
)
@pytest.mark.asyncio
async def test_invalid_oversize_or_reflected_sdp_stops_before_sideband(rig, body):
    rig.http.body = body
    with pytest.raises(SubscriptionTransportError) as caught:
        await connect(rig)
    assert TOKEN not in str(caught.value)
    assert not rig.aio.calls and rig.peer.closed


@pytest.mark.parametrize("failing", ["http", "aio"])
@pytest.mark.asyncio
async def test_network_exception_does_not_expose_credentials(rig, failing):
    getattr(rig, failing).failure = RuntimeError(f"Authorization: Bearer {TOKEN}")
    with pytest.raises(SubscriptionTransportError) as caught:
        await connect(rig)
    assert TOKEN not in str(caught.value)
    assert rig.transport.closed and rig.peer.closed


@pytest.mark.asyncio
async def test_startup_timeout_and_external_cancellation_close_media(rig):
    async def never():
        await asyncio.Event().wait()

    rig.peer.createOffer = never
    with pytest.raises(SubscriptionTransportError):
        await connect(rig)
    assert rig.peer.closed and rig.transport.closed


@pytest.mark.asyncio
async def test_cancel_during_negotiation_closes_media(rig):
    started = asyncio.Event()

    async def never():
        started.set()
        await asyncio.Event().wait()

    rig.peer.createOffer = never
    task = asyncio.create_task(connect(rig))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.peer.closed and rig.transport.closed


@pytest.mark.asyncio
async def test_external_close_cancels_pending_connect(rig):
    started = asyncio.Event()

    async def never():
        started.set()
        await asyncio.Event().wait()

    rig.peer.createOffer = never
    task = asyncio.create_task(connect(rig))
    await started.wait()
    await rig.transport.close()
    assert task.cancelled() and rig.peer.closed


@pytest.mark.asyncio
async def test_pcm_track_paces_mono_24k_and_mute_clears_queued_audio(rig):
    await connect(rig)
    await rig.transport.send_audio(b"\x10\x01" * FRAME_SAMPLES, PCM_RATE)
    frame = await rig.peer.track.recv()
    assert frame.sample_rate == PCM_RATE and frame.samples == FRAME_SAMPLES
    assert len(frame.layout.channels) == 1
    assert bytes(frame.planes[0]) == b"\x10\x01" * FRAME_SAMPLES
    await rig.transport.send_audio(b"\x10\x01" * FRAME_SAMPLES, PCM_RATE)
    rig.transport.set_muted(True)
    await rig.transport.send_audio(b"\x10\x01" * FRAME_SAMPLES, PCM_RATE)
    muted = await rig.peer.track.recv()
    assert bytes(muted.planes[0]) == bytes(FRAME_SAMPLES * 2)
    rig.transport.set_muted(False)
    await rig.transport.send_audio(b"\x11\x01" * FRAME_SAMPLES, PCM_RATE)
    assert bytes((await rig.peer.track.recv()).planes[0]) == b"\x11\x01" * FRAME_SAMPLES
    await rig.transport.close()


@pytest.mark.asyncio
async def test_actual_pyav_resamples_stereo_input_to_mono_24k(rig):
    await connect(rig)
    await rig.transport.send_audio(b"\x00\x10\x00\x10" * 1920, 48000, 2)
    frame = await rig.peer.track.recv()
    assert frame.sample_rate == PCM_RATE and len(frame.layout.channels) == 1
    assert any(bytes(frame.planes[0]))
    assert len(rig.peer.track._pending) < 1920 * 4
    await rig.transport.close()


@pytest.mark.asyncio
async def test_mute_after_resampling_prevents_queued_audio(rig):
    await connect(rig)
    converter = av.AudioResampler(format="s16", layout="mono", rate=PCM_RATE)

    def resample(frame):
        output = converter.resample(frame)
        rig.transport.set_muted(True)
        return output

    rig.transport._input_format = (48000, 2)
    rig.transport._input_resampler = SimpleNamespace(resample=resample)
    await rig.transport.send_audio(b"\x00\x10\x00\x10" * 1920, 48000, 2)
    assert not rig.peer.track._pending
    await rig.transport.close()


@pytest.mark.asyncio
async def test_pcm_alignment_and_bounded_audio_queue(rig):
    await connect(rig)
    with pytest.raises(SubscriptionTransportError, match="aligned"):
        await rig.transport.send_audio(b"\x00", PCM_RATE)
    with pytest.raises(SubscriptionTransportError, match="aligned"):
        await rig.transport.send_audio(b"\x00\x00", PCM_RATE, 3)
    with pytest.raises(SubscriptionTransportError, match="five seconds"):
        await rig.transport.send_audio(bytes(MAX_PENDING_PCM_BYTES + 2), PCM_RATE)
    await rig.transport.close()


@pytest.mark.asyncio
async def test_media_only_output_resampling_and_sideband_audio_deduplication(rig):
    await connect(rig)
    incoming = asyncio.Queue()
    remote = SimpleNamespace(kind="audio", recv=incoming.get)
    rig.peer.handlers["track"](remote)
    frame = av.AudioFrame(format="s16", layout="stereo", samples=1920)
    frame.sample_rate = 48000
    frame.planes[0].update(b"\x00\x10\x00\x10" * 1920)
    await incoming.put(frame)
    await rig.aio.socket.emit({"type": "session.output_audio.delta", "delta": "AA=="})
    await rig.aio.socket.emit({"type": "output_audio.delta", "audio": "AA=="})
    await rig.aio.socket.emit({"type": "session.started", "session": {}})
    await drain_until(lambda: len(rig.audio) == 1 and len(rig.events) == 1)
    data, rate, channels = rig.audio[0]
    assert data and any(data) and rate == PCM_RATE and channels == 1
    assert rig.events == [
        {"type": "session.started", "session": {"id": "rtc_test_call"}}
    ]
    await rig.transport.close()
    assert not rig.transport._tasks


@pytest.mark.asyncio
async def test_provider_error_redacts_message_and_closes_resources(rig):
    await connect(rig)
    await rig.aio.socket.emit(
        {"type": "error", "error": {"code": "invalid_token", "message": TOKEN}}
    )
    await drain_until(lambda: rig.peer.closed)
    assert rig.events[0]["error"]["code"] == "invalid_token"
    assert TOKEN not in repr(rig.events)
    assert rig.aio.socket.closed and rig.transport.closed


@pytest.mark.parametrize(
    "message",
    [
        SimpleNamespace(type="text", data="[1,2]"),
        SimpleNamespace(type="text", data="not JSON"),
        SimpleNamespace(type="closed", data=""),
    ],
)
@pytest.mark.asyncio
async def test_malformed_or_disconnected_sideband_reports_failure_and_closes(
    rig, message
):
    await connect(rig)
    await rig.aio.socket.queue.put(message)
    await drain_until(lambda: rig.peer.closed)
    assert rig.events[0]["type"] == "error"
    assert rig.events[0]["error"]["code"] == "connection_lost"
    assert rig.aio.socket.closed and rig.transport.closed


@pytest.mark.asyncio
async def test_session_finalization_preserves_usage_and_closes_resources(rig):
    await connect(rig)
    await rig.aio.socket.emit({"type": "session.closed", "usage": {"audio_seconds": 7}})
    await drain_until(lambda: rig.peer.closed)
    assert rig.transport.finalized
    assert rig.transport.final_usage == {"audio_seconds": 7}
    assert rig.events[0]["type"] == "session.closed"


@pytest.mark.asyncio
async def test_send_uses_subscription_wire_and_rejects_sideband_audio(rig):
    await connect(rig)
    event = delegation_context_events(
        "delegation_test", "The lookup finished.", spoken=True
    )[0]
    await rig.transport.send_event(event)
    assert rig.aio.socket.sent == [event]
    with pytest.raises(SubscriptionTransportError, match="media track"):
        await rig.transport.send_event(
            {"type": "session.input_audio.append", "audio": "AAAA"}
        )
    await rig.transport.close()


@pytest.mark.asyncio
async def test_send_failure_is_redacted_and_releases_resources(rig):
    await connect(rig)
    rig.aio.socket.failure = RuntimeError(TOKEN)
    with pytest.raises(SubscriptionTransportError) as caught:
        await rig.transport.send_event(
            {"type": "session.instructions.append", "content": "Hello"}
        )
    assert TOKEN not in str(caught.value) and TOKEN not in repr(rig.events)
    assert rig.peer.closed and rig.aio.socket.closed
    assert rig.events[0]["error"]["code"] == "connection_lost"


@pytest.mark.asyncio
async def test_no_automatic_reconnect_or_replay_after_close(rig):
    await connect(rig)
    await rig.transport.close()
    await rig.transport.close()
    with pytest.raises(SubscriptionTransportError, match="one session"):
        await connect(rig)
    with pytest.raises(SubscriptionTransportError, match="closed"):
        await rig.transport.send_audio(bytes(100), PCM_RATE)
    assert len(rig.http.calls) == 1


@pytest.mark.asyncio
async def test_input_rate_change_discards_previous_resampler_history(rig):
    await connect(rig)
    await rig.transport.send_audio(b"\x00\x10\x00\x10" * 1920, 48000, 2)
    first_resampler = rig.transport._input_resampler
    await rig.transport.send_audio(bytes(960), PCM_RATE)
    assert rig.transport._input_resampler is None
    await rig.transport.send_audio(bytes(7680), 48000, 2)
    assert rig.transport._input_resampler is not first_resampler
    await rig.transport.close()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize(
    "cross_origin", [False, True], ids=["same-origin", "cross-origin"]
)
@pytest.mark.asyncio
async def test_real_websocket_redirect_cannot_forward_credentials(
    rig, status, cross_origin
):
    source_requests, target_requests = [], []

    async def target(request):
        target_requests.append(dict(request.headers))
        return web.Response(text="Redirect target must not be requested")

    target_app = web.Application()
    target_app.router.add_get("/redirect-target", target)
    async with TestServer(target_app, host="127.0.0.1") as target_server:

        async def redirect(request):
            source_requests.append(dict(request.headers))
            destination = (
                str(target_server.make_url("/redirect-target"))
                if cross_origin
                else "/redirect-target"
            )
            return web.Response(status=status, headers={"Location": destination})

        source_app = web.Application()
        source_app.router.add_get("/sideband", redirect)
        source_app.router.add_get("/redirect-target", target)
        async with TestServer(source_app, host="127.0.0.1") as source_server:

            @asynccontextmanager
            async def local_session(*, trace_configs):
                async with aiohttp.ClientSession(trace_configs=trace_configs) as client:

                    def connect_local(url, **kwargs):
                        assert url == "wss://api.openai.com/v1/live/rtc_test_call"
                        return client.ws_connect(
                            source_server.make_url("/sideband"), **kwargs
                        )

                    yield SimpleNamespace(ws_connect=connect_local)

            rig.transport._aiohttp = SimpleNamespace(
                TraceConfig=aiohttp.TraceConfig,
                ClientSession=local_session,
                WSMsgType=aiohttp.WSMsgType,
            )
            rig.transport._connect_timeout = 2
            with pytest.raises(
                SubscriptionTransportError, match="redirects are not allowed"
            ) as caught:
                await connect(rig)

    assert len(source_requests) == 1
    assert source_requests[0]["Authorization"] == f"Bearer {TOKEN}"
    assert source_requests[0]["chatgpt-account-id"] == ACCOUNT
    assert source_requests[0]["session-id"] and source_requests[0]["thread-id"]
    assert target_requests == []
    assert rig.peer.closed and rig.transport.closed
    assert TOKEN not in str(caught.value) and ACCOUNT not in str(caught.value)


@pytest.mark.parametrize("stage", ["task-drain", "resource-close"])
@pytest.mark.asyncio
async def test_close_survives_caller_cancellation_and_concurrent_close_waits(
    rig, stage
):
    await connect(rig)
    reached, release = asyncio.Event(), asyncio.Event()
    cleanup_completions = []

    if stage == "task-drain":
        started = asyncio.Event()

        async def delayed_task():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                reached.set()
                await release.wait()
                await rig.transport.close()
                cleanup_completions.append("task")

        rig.transport._spawn(delayed_task())
        await started.wait()
    else:

        async def delayed_resource():
            reached.set()
            await release.wait()
            cleanup_completions.append("resource")

        rig.transport._stack.push_async_callback(delayed_resource)

    first = asyncio.create_task(rig.transport.close())
    await reached.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(rig.transport.close())
    third = asyncio.create_task(rig.transport.close())
    await asyncio.sleep(0)
    assert not second.done() and not third.done()
    assert rig.transport.closed and not rig.peer.closed
    release.set()
    async with asyncio.timeout(1):
        await asyncio.gather(second, third)
    assert len(cleanup_completions) == 1
    assert rig.peer.closed and rig.aio.socket.closed and rig.aio.closed
    assert rig.peer.track.readyState == "ended"
    assert rig.transport._close_task.done()
    assert not rig.transport._tasks
