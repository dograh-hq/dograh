"""Keepalive-aware wrapper around the Dial media WebSocket.

Dial sends a ``ping_pong`` frame every ~2 s and treats the link as dead if
the same frame is not echoed within ~5 s, after which it drops the socket and
reconnects (https://docs.getdial.ai/api-reference/self-hosted-audio-protocol/ping-pong).
A frame serializer cannot answer it — ``deserialize`` has no way to write —
and nothing reads the socket at all while the run is being resolved and the
pipeline is starting, which can take longer than Dial's deadline.

So one pump task owns ``receive()`` from the moment the socket is accepted:
it echoes every ``ping_pong`` immediately and queues everything else for
whoever reads next — the route's handshake, then pipecat's input transport.
While nobody is reading, only the most recent frames are kept, so the
pipeline does not start by replaying seconds of stale caller audio.
"""

import asyncio
import json
from typing import Any, Optional

from fastapi import WebSocket
from loguru import logger

# ~1 s of 20 ms media frames. Older frames are dropped while nobody reads.
_MAX_BUFFERED_FRAMES = 50

_DISCONNECT = {"type": "websocket.disconnect", "code": 1006}


class DialWebSocket:
    """Proxies a Starlette ``WebSocket``; ``receive()`` never returns pings."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pump_task: Optional[asyncio.Task] = None
        self._closed = False

    def start(self) -> None:
        if self._pump_task is None:
            self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            while True:
                message = await self._websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    self._enqueue(message)
                    return

                text = message.get("text")
                if text and await self._answer_ping(text):
                    continue

                self._enqueue(message)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Receiving after the peer closed raises in Starlette; surface it
            # to the reader as an ordinary disconnect.
            logger.debug(f"Dial socket pump stopped: {e}")
            self._enqueue(dict(_DISCONNECT))

    async def _answer_ping(self, text: str) -> bool:
        if "ping_pong" not in text:
            return False
        try:
            frame = json.loads(text)
        except json.JSONDecodeError:
            return False
        if not isinstance(frame, dict) or frame.get("type") != "ping_pong":
            return False
        try:
            await self._websocket.send_text(text)
        except Exception as e:
            logger.debug(f"Could not echo Dial ping_pong: {e}")
        return True

    def _enqueue(self, message: dict[str, Any]) -> None:
        while self._queue.qsize() >= _MAX_BUFFERED_FRAMES:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._queue.put_nowait(message)

    async def receive(self) -> dict[str, Any]:
        """Next non-keepalive ASGI message, or a disconnect once the socket ends."""
        if self._pump_task is None:
            self.start()
        if self._closed and self._queue.empty():
            return dict(_DISCONNECT)
        message = await self._queue.get()
        if message.get("type") == "websocket.disconnect":
            self._closed = True
        return message

    async def receive_json_frame(self, timeout: float) -> Optional[dict[str, Any]]:
        """Next JSON text frame within ``timeout`` seconds; None on close/timeout."""
        try:
            async with asyncio.timeout(timeout):
                while True:
                    message = await self.receive()
                    if message.get("type") == "websocket.disconnect":
                        return None
                    text = message.get("text")
                    if not text:
                        continue
                    try:
                        frame = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(frame, dict):
                        return frame
        except TimeoutError:
            return None

    async def send_json_frame(self, frame: dict[str, Any]) -> None:
        await self._websocket.send_text(json.dumps(frame))

    async def close(self, code: int = 1000, reason: Optional[str] = None) -> None:
        await self.stop()
        try:
            await self._websocket.close(code=code, reason=reason)
        except RuntimeError:
            pass

    async def stop(self) -> None:
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()
            try:
                await self._pump_task
            except (asyncio.CancelledError, Exception):
                pass

    def __getattr__(self, name: str) -> Any:
        # Everything pipecat touches besides receive()/close() (send_text,
        # client_state, application_state, ...) goes straight to the socket.
        return getattr(self._websocket, name)
