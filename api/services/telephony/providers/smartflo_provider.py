"""
Smartflo (Tata Tele Business) bi-directional voice streaming provider.

This module implements the Smartflo WebSocket streaming protocol for the
Dograh voice AI platform. It handles the full call lifecycle:

  Connection Establishment:
    1. Dograh accepts the WebSocket connection (HTTP 101).
    2. Dograh immediately sends {"event": "connected"} — Smartflo REQUIRES
       this ACK before it will send the "start" event. Without it, Smartflo
       closes the connection with code 1005 (no status received).

  Stream Initialization:
    3. Smartflo sends {"event": "start", "start": {...}, "streamSid": "MZ..."}
       containing call metadata (callSid, from/to numbers, media format).

  Audio Streaming:
    4. Smartflo streams {"event": "media", "media": {"payload": "<base64>"}}
       frames containing μ-law 8kHz audio from the caller (~every 100ms).
    5. Dograh sends back {"event": "media", "media": {"payload": "<base64>"}}
       frames containing μ-law 8kHz TTS audio to play to the caller.
       Payloads MUST be multiples of 160 bytes (= 20ms of μ-law @ 8kHz).

  Stream Termination:
    6. Smartflo sends {"event": "stop"} when the call ends.
    7. Dograh can send {"event": "clear"} to flush the audio buffer.
    8. Dograh can send {"event": "mark"} to track when audio playback ends.

Reference:
  https://docs.smartflo.tatatelebusiness.com/docs/bi-directional-audio-streaming-integration-document
"""

import asyncio
import base64
import json
import uuid
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger
from starlette.websockets import WebSocketState

# ── Protocol constants ─────────────────────────────────────────────────────────
# Smartflo requires this to be sent immediately after WS upgrade.
CONNECTED_ACK = json.dumps({"event": "connected"})

# μ-law @ 8 kHz: 8000 samples/sec × 1 byte/sample = 8000 bytes/sec.
# 20ms frame = 160 bytes. Smartflo requires payloads to be multiples of 160.
MULAW_CHUNK_BYTES = 160
MULAW_SILENCE_BYTE = 0x7F  # μ-law silence value


# ── Frame builders ─────────────────────────────────────────────────────────────

def _build_media_frame(stream_sid: str, audio: bytes, chunk_num: int) -> str:
    """Build a Smartflo media event frame containing base64-encoded μ-law audio."""
    return json.dumps({
        "event": "media",
        "streamSid": stream_sid,
        "media": {
            "payload": base64.b64encode(audio).decode("utf-8"),
            "chunk": chunk_num,
        },
    })


def _build_mark_frame(stream_sid: str, name: str) -> str:
    """Build a mark event to be notified when audio playback ends."""
    return json.dumps({
        "event": "mark",
        "streamSid": stream_sid,
        "mark": {"name": name},
    })


def _build_clear_frame(stream_sid: str) -> str:
    """Build a clear event to flush Smartflo's audio buffer (for barge-in)."""
    return json.dumps({
        "event": "clear",
        "streamSid": stream_sid,
    })


# ── Transport ──────────────────────────────────────────────────────────────────

class SmartfloTransport:
    """
    High-level WebSocket transport for the Smartflo voice streaming protocol.

    Wraps a FastAPI WebSocket connection and provides:
      - handshake()    — sends connected ACK, receives start event
      - recv_audio()   — receives one frame; returns raw μ-law bytes or None
      - send_audio()   — sends TTS μ-law audio back to the caller
      - send_mark()    — notifies when bot audio playback ends
      - send_clear()   — interrupts buffered audio (e.g. on barge-in)
      - close()        — gracefully closes the connection

    Usage::

        transport = SmartfloTransport(websocket)
        ok = await transport.handshake()
        if not ok:
            return

        call_info = transport.call_info  # callSid, streamSid, from/to numbers

        while True:
            audio = await transport.recv_audio()  # raises WebSocketDisconnect on stop
            if audio:
                response = await ai_pipeline(audio)
                await transport.send_audio(response)
    """

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._stream_sid: str = ""
        self._chunk_out: int = 0
        # Populated after a successful handshake()
        self.call_info: dict = {}

    # ── Handshake ──────────────────────────────────────────────────────────────

    async def handshake(self) -> bool:
        """
        Execute the Smartflo connection handshake.

        Sends the mandatory ``{"event": "connected"}`` ACK, then waits for
        Smartflo's ``{"event": "start"}`` message that carries call metadata.

        Returns:
            True  — handshake succeeded; ``self.call_info`` is populated.
            False — handshake failed (timeout, wrong event, bad JSON, etc.).
                    Caller should close the connection and return.
        """
        # Step 2: Send connected ACK — MUST happen before anything else.
        await self._ws.send_text(CONNECTED_ACK)
        logger.info("[Smartflo] → sent connected ACK")

        # Step 3: Wait for start event (Smartflo sends this after our ACK).
        try:
            raw = await asyncio.wait_for(self._ws.receive_text(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.error("[Smartflo] Timed out waiting for 'start' event (15s)")
            return False
        except Exception as exc:
            logger.error(f"[Smartflo] Error receiving start event: {exc}")
            return False

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(f"[Smartflo] Invalid JSON in start message: {exc}. raw={raw[:200]}")
            return False

        event = data.get("event")
        if event != "start":
            logger.error(f"[Smartflo] Expected 'start' event, got '{event}'")
            return False

        start = data.get("start", {})
        self._stream_sid = (
            data.get("streamSid")
            or start.get("streamSid")
            or str(uuid.uuid4())
        )
        self.call_info = {
            "stream_sid":  self._stream_sid,
            "call_sid":    start.get("callSid", ""),
            "account_sid": start.get("accountSid", ""),
            "from_number": start.get("from", ""),
            "to_number":   start.get("to", ""),
            "direction":   start.get("direction", "inbound"),
            "media_format": start.get("mediaFormat", {
                "encoding":  "audio/x-mulaw",
                "sampleRate": 8000,
            }),
        }
        logger.info(
            f"[Smartflo] ← start received | "
            f"callSid={self.call_info['call_sid']} "
            f"from={self.call_info['from_number']} "
            f"to={self.call_info['to_number']} "
            f"streamSid={self._stream_sid}"
        )
        return True

    # ── Inbound frames ─────────────────────────────────────────────────────────

    async def recv_audio(self) -> Optional[bytes]:
        """
        Receive one frame from Smartflo.

        Returns:
            bytes — raw μ-law audio from the caller (for ``media`` events).
            None  — non-audio frame (e.g. ``dtmf``, ``mark``); caller can ignore.

        Raises:
            WebSocketDisconnect — when Smartflo sends ``{"event": "stop"}``
                                  or the connection drops.
        """
        raw = await self._ws.receive_text()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[Smartflo] Non-JSON frame (ignored): {raw[:100]}")
            return None

        event = data.get("event")

        if event == "media":
            try:
                return base64.b64decode(data["media"]["payload"])
            except (KeyError, Exception) as exc:
                logger.warning(f"[Smartflo] Failed to decode media payload: {exc}")
                return None

        if event == "stop":
            reason = data.get("stop", {}).get("reason", "unknown")
            logger.info(f"[Smartflo] ← stop received | reason={reason}")
            raise WebSocketDisconnect(code=1000, reason="call stopped")

        if event == "dtmf":
            digit = data.get("dtmf", {}).get("digit", "?")
            logger.info(f"[Smartflo] ← dtmf | digit={digit}")
            return None

        if event == "mark":
            name = data.get("mark", {}).get("name", "")
            logger.debug(f"[Smartflo] ← mark | name={name}")
            return None

        logger.debug(f"[Smartflo] ← unhandled event: {event}")
        return None

    # ── Outbound frames ────────────────────────────────────────────────────────

    async def send_audio(self, mulaw_bytes: bytes) -> None:
        """
        Send μ-law 8kHz TTS audio to be played to the caller.

        Args:
            mulaw_bytes: Raw μ-law audio bytes (any length). Automatically
                         padded to the next multiple of MULAW_CHUNK_BYTES
                         (160 bytes = 20ms) and sent in 160-byte chunks.
        """
        if not self._stream_sid:
            logger.warning("[Smartflo] send_audio called before handshake")
            return

        # Pad to nearest multiple of MULAW_CHUNK_BYTES (Smartflo requirement).
        remainder = len(mulaw_bytes) % MULAW_CHUNK_BYTES
        if remainder:
            mulaw_bytes += bytes([MULAW_SILENCE_BYTE] * (MULAW_CHUNK_BYTES - remainder))

        # Send in MULAW_CHUNK_BYTES chunks.
        for i in range(0, len(mulaw_bytes), MULAW_CHUNK_BYTES):
            self._chunk_out += 1
            frame = _build_media_frame(
                self._stream_sid,
                mulaw_bytes[i : i + MULAW_CHUNK_BYTES],
                self._chunk_out,
            )
            await self._ws.send_text(frame)

    async def send_mark(self, name: str = "end") -> None:
        """
        Send a mark event so Smartflo notifies us when TTS playback ends.

        Args:
            name: Arbitrary label; Smartflo echoes it back with the same name
                  once the corresponding audio has finished playing.
        """
        if self._stream_sid:
            await self._ws.send_text(_build_mark_frame(self._stream_sid, name))
            logger.debug(f"[Smartflo] → mark | name={name}")

    async def send_clear(self) -> None:
        """
        Flush Smartflo's audio buffer immediately.

        Use this to interrupt the bot's speech when the caller starts talking
        (barge-in / interruption detection). Smartflo will echo back any
        pending mark events once the buffer is cleared.
        """
        if self._stream_sid:
            await self._ws.send_text(_build_clear_frame(self._stream_sid))
            logger.debug("[Smartflo] → clear (audio buffer flushed)")

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Gracefully close the WebSocket if still open."""
        if self._ws.client_state != WebSocketState.DISCONNECTED:
            try:
                await self._ws.close()
            except Exception:
                pass  # Already closed
