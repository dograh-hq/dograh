"""Experimental subscription WebRTC transport, adapted from Hermes Talk (MIT).

The subscription compatibility wire shapes derive from OpenClaw 76378ddb.
See THIRD_PARTY_NOTICES.md. This is not the public OpenAI Live API transport.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from fractions import Fraction
from urllib.parse import urlparse

SUBSCRIPTION_CALL_URL = "https://chatgpt.com/backend-api/codex/realtime/calls?intent=quicksilver&architecture=avas"
PCM_RATE = 24000
FRAME_SAMPLES = 480
MAX_PENDING_PCM_BYTES = PCM_RATE * 2 * 5
MAX_SDP_BYTES = 256 * 1024
MAX_WIRE_BYTES = 2 * 1024 * 1024
CONNECT_TIMEOUT_S = 30.0
MAX_APPEND_BYTES = 500
SUBSCRIPTION_VOICES = (
    "arbor",
    "breeze",
    "cove",
    "ember",
    "juniper",
    "maple",
    "sol",
    "spruce",
    "vale",
)
_CALL_ID = re.compile(
    r"^(?:rtc_[A-Za-z0-9_-]{1,128}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_DELEGATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,512}$")


class SubscriptionTransportError(RuntimeError):
    """Public, redacted failure; never wrap a provider exception with its text."""

    def __init__(self, message: str, *, code: str = "transport_failed"):
        super().__init__(message)
        self.code = code


async def _reject_sideband_redirect(session, trace_context, params) -> None:
    # aiohttp ws_connect has no allow_redirects option; this runs before follow.
    params.response.close()
    raise SubscriptionTransportError(
        "Subscription voice sideband redirects are not allowed"
    )


def validate_sdp(sdp: str) -> str:
    if (
        not isinstance(sdp, str)
        or len(sdp.encode("utf-8")) > MAX_SDP_BYTES
        or "\x00" in sdp
        or not re.match(r"^v=0\r?\n", sdp)
        or not re.search(r"(?:^|\n)m=audio ", sdp)
    ):
        raise SubscriptionTransportError(
            "Subscription voice returned an invalid audio SDP"
        )
    return sdp


def subscription_call_id(headers: Mapping) -> str:
    headers = {str(key).lower(): value for key, value in headers.items()}
    session_id = headers.get("openai-session-id", "")
    location = headers.get("location", "")
    if location:
        if not isinstance(location, str) or len(location) > 512:
            raise SubscriptionTransportError(
                "Subscription voice returned an invalid call location"
            )
        parsed = urlparse(location)
        if parsed.netloc and parsed.netloc not in {"api.openai.com", "chatgpt.com"}:
            raise SubscriptionTransportError(
                "Subscription voice returned an untrusted call location"
            )
        if parsed.query or parsed.fragment or parsed.scheme not in {"", "https"}:
            raise SubscriptionTransportError(
                "Subscription voice returned an invalid call location"
            )
        candidates = [
            part for part in parsed.path.split("/") if _CALL_ID.fullmatch(part)
        ]
        if candidates:
            call_id = candidates[-1]
            if session_id and session_id != call_id:
                raise SubscriptionTransportError(
                    "Subscription voice returned conflicting call identifiers"
                )
            return call_id
    if isinstance(session_id, str) and _CALL_ID.fullmatch(session_id):
        return session_id
    raise SubscriptionTransportError(
        "Subscription voice did not return a valid call identifier"
    )


def build_subscription_session(*, model: str, voice: str, instructions: str) -> dict:
    if model != "gpt-live-1-codex" or voice not in SUBSCRIPTION_VOICES:
        raise SubscriptionTransportError(
            "Unsupported subscription voice model or voice"
        )
    if not isinstance(instructions, str):
        raise SubscriptionTransportError("Subscription voice instructions must be text")
    return {
        "model": model,
        "instructions": instructions,
        "audio": {"output": {"voice": voice}},
        "delegation": {"type": "client"},
    }


def chunk_append_text(text: str) -> tuple[str, ...]:
    chunks, current, size = [], "", 0
    for character in text:
        width = len(character.encode("utf-8"))
        if size + width > MAX_APPEND_BYTES:
            chunks.append(current)
            current, size = "", 0
        current += character
        size += width
    if current:
        chunks.append(current)
    return tuple(chunks)


def context_append_events(
    text: str, *, kind: str = "instructions", delegation_id: str | None = None
) -> tuple[dict, ...]:
    if kind not in {"instructions", "thinking", "commentary"}:
        raise SubscriptionTransportError("Unsupported subscription context kind")
    if delegation_id is not None:
        return delegation_context_events(
            delegation_id, text, spoken=kind == "commentary"
        )
    channel = {
        "instructions": "developer",
        "thinking": "commentary",
        "commentary": "speakable",
    }[kind]
    return tuple(
        {
            "type": "session.context.append",
            "channel": channel,
            "content": [{"type": "input_text", "text": chunk}],
        }
        for chunk in chunk_append_text(text)
    )


def delegation_context_events(
    delegation_id: str, text: str, *, spoken: bool
) -> tuple[dict, ...]:
    if not isinstance(delegation_id, str) or not _DELEGATION_ID.fullmatch(
        delegation_id
    ):
        raise SubscriptionTransportError("Invalid subscription delegation identifier")
    return tuple(
        {
            "type": "delegation.context.append",
            "delegation_item_id": delegation_id,
            "channel": "speakable" if spoken else "commentary",
            "content": [{"type": "input_text", "text": chunk}],
        }
        for chunk in chunk_append_text(text)
    )


def _load_media_modules():
    try:
        import aiortc
        import av
    except ImportError:
        raise SubscriptionTransportError(
            "Subscription voice requires the aiortc and av media dependencies"
        ) from None
    return aiortc, av


def create_pcm_audio_track(*, rtc_module, av_module):
    class QueuedAudioStreamTrack(rtc_module.AudioStreamTrack):
        def __init__(self):
            super().__init__()
            self._pending = bytearray()
            self._pts = 0
            self._deadline = None

        def append(self, pcm: bytes) -> None:
            if self.readyState != "live":
                raise SubscriptionTransportError("Subscription audio track is closed")
            if not isinstance(pcm, bytes) or len(pcm) % 2:
                raise SubscriptionTransportError(
                    "Subscription input requires aligned PCM16"
                )
            if len(self._pending) + len(pcm) > MAX_PENDING_PCM_BYTES:
                raise SubscriptionTransportError(
                    "Subscription input audio queue exceeded five seconds"
                )
            self._pending.extend(pcm)

        def clear(self) -> None:
            self._pending.clear()

        async def recv(self):
            if self.readyState != "live":
                raise rtc_module.mediastreams.MediaStreamError
            loop = asyncio.get_running_loop()
            if self._deadline is None:
                self._deadline = loop.time()
            await asyncio.sleep(max(0, self._deadline - loop.time()))
            if self.readyState != "live":
                raise rtc_module.mediastreams.MediaStreamError
            self._deadline = max(self._deadline + 0.020, loop.time())
            count = min(FRAME_SAMPLES * 2, len(self._pending))
            data = bytes(self._pending[:count]) + bytes(FRAME_SAMPLES * 2 - count)
            del self._pending[:count]
            frame = av_module.AudioFrame(
                format="s16", layout="mono", samples=FRAME_SAMPLES
            )
            frame.planes[0].update(data)
            frame.sample_rate = PCM_RATE
            frame.time_base = Fraction(1, PCM_RATE)
            frame.pts = self._pts
            self._pts += FRAME_SAMPLES
            return frame

        def stop(self):
            self.clear()
            super().stop()

    return QueuedAudioStreamTrack()


class OpenAILiveSubscriptionTransport:
    def __init__(
        self,
        *,
        on_event: Callable[[dict], Awaitable[None]],
        on_audio: Callable[[bytes, int, int], Awaitable[None]],
        http_client=None,
        aiohttp_module=None,
        rtc_module=None,
        av_module=None,
        connect_timeout: float = CONNECT_TIMEOUT_S,
    ):
        self._on_event = on_event
        self._on_audio = on_audio
        self._http = http_client
        self._aiohttp = aiohttp_module
        self._rtc = rtc_module
        self._av = av_module
        self._connect_timeout = connect_timeout
        self._stack = AsyncExitStack()
        self._peer = None
        self._track = None
        self._ws = None
        self._tasks = set()
        self._connect_task = None
        self._close_task = None
        self._closing_tasks = set()
        self._input_resampler = None
        self._input_format = None
        self._muted = False
        self._started = False
        self._closed = False
        self.session_id = None
        self.finalized = False
        self.final_usage = None

    @property
    def closed(self) -> bool:
        return self._closed

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _negotiate(self, headers: dict, session: dict) -> str:
        client = self._http
        if client is None:
            import httpx

            client = await self._stack.enter_async_context(
                httpx.AsyncClient(timeout=self._connect_timeout)
            )
        try:
            async with client.stream(
                "POST",
                SUBSCRIPTION_CALL_URL,
                headers=headers,
                json={
                    "sdp": validate_sdp(self._peer.localDescription.sdp),
                    "session": session,
                },
                follow_redirects=False,
            ) as response:
                status = response.status_code
                if status not in {200, 201}:
                    code = (
                        "authentication_error"
                        if status in {401, 403}
                        else "rate_limited" if status == 429 else "negotiation_failed"
                    )
                    raise SubscriptionTransportError(
                        f"Subscription voice session creation failed (HTTP {status})",
                        code=code,
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_SDP_BYTES:
                        raise SubscriptionTransportError(
                            "Subscription voice SDP exceeded the size limit"
                        )
                    body.extend(chunk)
                self.session_id = subscription_call_id(response.headers)
                answer = validate_sdp(body.decode("utf-8"))
                token = headers["Authorization"].removeprefix("Bearer ")
                if token in answer or headers["chatgpt-account-id"] in answer:
                    raise SubscriptionTransportError(
                        "Subscription voice SDP reflected private credentials"
                    )
                return answer
        except SubscriptionTransportError:
            raise
        except Exception:  # noqa: BLE001 - external errors may contain credentials
            raise SubscriptionTransportError(
                "Subscription voice session negotiation failed"
            ) from None

    async def connect(self, access_token: str, account_id: str, session: dict) -> None:
        if self._started or self._closed:
            raise SubscriptionTransportError(
                "A subscription transport supports one session only"
            )
        self._started = True
        self._connect_task = asyncio.current_task()
        try:
            for value in (access_token, account_id):
                if (
                    not isinstance(value, str)
                    or not value
                    or value != value.strip()
                    or any(
                        ord(character) < 32 or ord(character) == 127
                        for character in value
                    )
                ):
                    raise SubscriptionTransportError(
                        "Subscription voice credential is missing or invalid"
                    )
            session = build_subscription_session(
                model=session["model"],
                voice=session["audio"]["output"]["voice"],
                instructions=session["instructions"],
            )
            if self._rtc is None or self._av is None:
                self._rtc, self._av = _load_media_modules()
            if self._aiohttp is None:
                import aiohttp

                self._aiohttp = aiohttp
            headers = {
                "Authorization": f"Bearer {access_token}",
                "OpenAI-Alpha": "quicksilver=v2",
                "chatgpt-account-id": account_id,
                "session-id": str(uuid.uuid4()),
                "thread-id": str(uuid.uuid4()),
                "x-session-id": str(uuid.uuid4()),
            }
            self._peer = self._rtc.RTCPeerConnection()
            self._track = create_pcm_audio_track(
                rtc_module=self._rtc, av_module=self._av
            )
            self._peer.addTrack(self._track)
            connected = asyncio.Event()

            @self._peer.on("connectionstatechange")
            async def on_state():
                state = self._peer.connectionState
                if state == "connected":
                    connected.set()
                elif state in {"failed", "closed", "disconnected"} and not self._closed:
                    connected.set()
                    self._spawn(self._fail("Subscription voice media disconnected"))

            @self._peer.on("track")
            def on_track(track):
                if track.kind == "audio":
                    self._spawn(self._read_audio(track))

            async with asyncio.timeout(self._connect_timeout):
                await self._peer.setLocalDescription(await self._peer.createOffer())
                answer = await self._negotiate(headers, session)
                redirect_guard = self._aiohttp.TraceConfig()
                redirect_guard.on_request_redirect.append(_reject_sideband_redirect)
                client = await self._stack.enter_async_context(
                    self._aiohttp.ClientSession(trace_configs=[redirect_guard])
                )
                self._ws = await self._stack.enter_async_context(
                    client.ws_connect(
                        f"wss://api.openai.com/v1/live/{self.session_id}",
                        headers=headers,
                        max_msg_size=MAX_WIRE_BYTES,
                        heartbeat=20,
                    )
                )
                self._spawn(self._read_sideband())
                await self._peer.setRemoteDescription(
                    self._rtc.RTCSessionDescription(sdp=answer, type="answer")
                )
                await connected.wait()
                if self._closed or self._peer.connectionState != "connected":
                    raise SubscriptionTransportError(
                        "Subscription voice media failed during startup"
                    )
        except asyncio.CancelledError:
            await self.close()
            raise
        except SubscriptionTransportError:
            await self.close()
            raise
        except Exception:  # noqa: BLE001 - external errors may contain credentials
            await self.close()
            raise SubscriptionTransportError(
                "Subscription voice connection failed"
            ) from None
        finally:
            self._connect_task = None

    async def _fail(self, message: str):
        if self._closed:
            return
        try:
            await self._on_event(
                {
                    "type": "error",
                    "error": {"code": "connection_lost", "message": message},
                }
            )
        finally:
            await self.close()

    async def _read_sideband(self):
        try:
            kinds = self._aiohttp.WSMsgType
            while not self._closed:
                message = await self._ws.receive()
                if message.type in (
                    kinds.CLOSE,
                    kinds.CLOSED,
                    kinds.CLOSING,
                    kinds.ERROR,
                ):
                    raise SubscriptionTransportError(
                        "Subscription voice sideband disconnected"
                    )
                if message.type not in (kinds.TEXT, kinds.BINARY):
                    continue
                if len(message.data) > MAX_WIRE_BYTES:
                    raise SubscriptionTransportError(
                        "Subscription voice event exceeded the size limit"
                    )
                event = json.loads(message.data)
                if not isinstance(event, dict):
                    raise SubscriptionTransportError(
                        "Subscription voice returned a malformed event"
                    )
                kind = event.get("type")
                if kind in {"session.output_audio.delta", "output_audio.delta"}:
                    continue
                if kind == "session.started":
                    session = event.get("session")
                    if isinstance(session, dict) and not session.get("id"):
                        event = {**event, "session": {**session, "id": self.session_id}}
                if kind == "error":
                    raw = event.get("error")
                    raw = raw if isinstance(raw, dict) else {}
                    code = raw.get("code", event.get("code"))
                    if code not in {
                        "authentication_error",
                        "invalid_token",
                        "token_expired",
                        "rate_limited",
                    }:
                        code = "provider_error"
                    event = {
                        "type": "error",
                        "error": {
                            "code": code,
                            "message": "Subscription voice rejected a session command",
                        },
                    }
                if kind == "session.closed":
                    self.finalized = True
                    usage = event.get("usage")
                    self.final_usage = (
                        dict(usage) if isinstance(usage, Mapping) else None
                    )
                await self._on_event(event)
                if kind in {"session.closed", "error"}:
                    await self.close()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - external errors may contain credentials
            await self._fail("Subscription voice sideband disconnected")

    async def _read_audio(self, track):
        try:
            converter = self._av.AudioResampler(
                format="s16", layout="mono", rate=PCM_RATE
            )
            while not self._closed:
                frame = await track.recv()
                for part in converter.resample(frame):
                    if part.samples:
                        await self._on_audio(
                            bytes(part.planes[0])[: part.samples * 2], PCM_RATE, 1
                        )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - external errors may contain credentials
            await self._fail("Subscription voice remote audio track ended")

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        if muted:
            if self._track is not None:
                self._track.clear()
            self._input_resampler = None
            self._input_format = None

    async def send_audio(self, pcm: bytes, sample_rate: int, channels: int = 1) -> None:
        if self._track is None or self._closed:
            raise SubscriptionTransportError("Subscription voice media is closed")
        if self._muted:
            return
        if (
            not isinstance(pcm, bytes)
            or channels not in {1, 2}
            or not isinstance(sample_rate, int)
            or not 8000 <= sample_rate <= 96000
            or len(pcm) % (2 * channels)
        ):
            raise SubscriptionTransportError(
                "Subscription input requires aligned mono or stereo PCM16"
            )
        if not pcm:
            return
        try:
            audio_format = (sample_rate, channels)
            if sample_rate == PCM_RATE and channels == 1:
                self._input_resampler = None
                self._input_format = audio_format
                chunks = (pcm,)
            else:
                if self._input_format != audio_format:
                    self._input_resampler = self._av.AudioResampler(
                        format="s16", layout="mono", rate=PCM_RATE
                    )
                    self._input_format = audio_format
                frame = self._av.AudioFrame(
                    format="s16",
                    layout="mono" if channels == 1 else "stereo",
                    samples=len(pcm) // (2 * channels),
                )
                frame.planes[0].update(pcm)
                frame.sample_rate = sample_rate
                chunks = tuple(
                    bytes(part.planes[0])[: part.samples * 2]
                    for part in self._input_resampler.resample(frame)
                    if part.samples
                )
            if not self._muted:
                for chunk in chunks:
                    self._track.append(chunk)
        except SubscriptionTransportError:
            raise
        except Exception:  # noqa: BLE001 - external errors may contain credentials
            raise SubscriptionTransportError(
                "Subscription input audio conversion failed"
            ) from None

    async def send_event(self, event: dict) -> None:
        if self._ws is None or self._closed:
            raise SubscriptionTransportError("Subscription voice sideband is closed")
        try:
            if (
                not isinstance(event, dict)
                or len(json.dumps(event).encode("utf-8")) > MAX_WIRE_BYTES
            ):
                raise SubscriptionTransportError(
                    "Subscription voice command exceeded the size limit"
                )
            if event.get("type") in {
                "session.input_audio.append",
                "input_audio_buffer.append",
            }:
                raise SubscriptionTransportError(
                    "Subscription voice audio must use the media track"
                )
            async with asyncio.timeout(10):
                await self._ws.send_json(event)
        except SubscriptionTransportError:
            raise
        except Exception:  # noqa: BLE001 - external errors may contain credentials
            await self._fail("Subscription voice command failed")
            raise SubscriptionTransportError(
                "Subscription voice command failed"
            ) from None

    async def close(self) -> None:
        current = asyncio.current_task()
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close_resources(current))
        # A task canceled by teardown may call close from its finally block.
        # Waiting here would make cleanup and that task wait on each other.
        if current in self._closing_tasks:
            return
        await asyncio.shield(self._close_task)

    async def _close_resources(self, initiator) -> None:
        tasks = tuple(task for task in self._tasks if task is not initiator)
        if self._connect_task is not None and self._connect_task is not initiator:
            tasks += (self._connect_task,)
        self._closing_tasks.update(tasks)
        try:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self._track is not None:
                self._track.stop()
            self._input_resampler = None
            try:
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(5):
                        await self._stack.aclose()
            finally:
                if self._peer is not None:
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(5):
                            await self._peer.close()
        finally:
            self._closing_tasks.clear()
