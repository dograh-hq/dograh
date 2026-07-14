"""Telnyx TTS WebSocket service wrapper.

Telnyx provides a streaming TTS WebSocket API at
wss://api.telnyx.com/v2/text-to-speech/speech?voice={voice_id}. Text frames
are sent as JSON and audio frames arrive as base64-encoded mp3. This wrapper
subclasses pipecat's WebsocketTTSService to integrate with the pipeline.
"""

import base64
import json
from typing import Any

import aiohttp
from loguru import logger

from pipecat.frames.frames import Frame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.tts_service import WebsocketTTSService


class TelnyxTTSService(WebsocketTTSService):
    """Telnyx streaming TTS over WebSocket.

    Sends text as JSON frames, receives base64-encoded mp3 audio. The
    WebSocket stays open for the session so consecutive sentences reuse
    the connection (low latency after the first phrase).
    """

    def __init__(
        self,
        *,
        api_key: str,
        voice: str = "Telnyx.NaturalHD.astra",
        model: str = "natural-hd",
        language: str = "en",
        speed: float = 1.0,
        sample_rate: int = 24000,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self._api_key = api_key
        self._voice = voice
        self._model = model
        self._language = language
        self._speed = speed
        self._sample_rate = sample_rate

    def _build_url(self) -> str:
        return f"wss://api.telnyx.com/v2/text-to-speech/speech?voice={self._voice}"

    async def _connect(self) -> aiohttp.ClientWebSocketResponse:
        session = aiohttp.ClientSession()
        self._session = session
        ws = await session.ws_connect(
            self._build_url(),
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        # Send an init frame (single space) per Telnyx protocol
        await ws.send_str(" ")
        return ws

    async def _disconnect(self) -> None:
        if hasattr(self, "_session") and self._session and not self._session.closed:
            await self._session.close()

    async def _send_text(self, ws: aiohttp.ClientWebSocketResponse, text: str) -> None:
        await ws.send_str(text)

    async def _receive_audio(self, ws: aiohttp.ClientWebSocketResponse) -> bytes | None:
        msg = await ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                payload = json.loads(msg.data)
                audio_b64 = payload.get("audio") or payload.get("data")
                if audio_b64:
                    return base64.b64decode(audio_b64)
            except (json.JSONDecodeError, KeyError):
                pass
        return None

    async def _stop_signal(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        await ws.send_str("")  # empty text frame = stop
