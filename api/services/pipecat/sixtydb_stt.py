"""60db streaming speech-to-text service."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

from loguru import logger
from websockets.protocol import State

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import WebsocketSTTService
from pipecat.utils.time import time_now_iso8601

SIXTYDB_STT_HANDSHAKE_TIMEOUT_SECONDS = 10.0


def sixtydb_stt_websocket_url(base_url: str) -> str:
    """Build the authenticated 60db STT WebSocket URL."""
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    scheme = {"https": "wss", "http": "ws"}.get(parsed.scheme, parsed.scheme)
    if scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError("Invalid 60db base URL")
    return urlunparse((scheme, parsed.netloc, "/ws/stt", "", "", ""))


@dataclass
class SixtydbSTTSettings(STTSettings):
    """Runtime settings for SixtydbSTTService."""

    model: str = "60db-stt-v01"
    language: str = "auto"
    keywords: list[str] = field(default_factory=list)


class SixtydbSTTService(WebsocketSTTService):
    """60db Garuda speech-to-text over its streaming WebSocket API."""

    Settings = SixtydbSTTSettings
    _settings: SixtydbSTTSettings

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.60db.ai",
        sample_rate: int | None = None,
        settings: SixtydbSTTSettings | None = None,
        **kwargs,
    ):
        default_settings = self.Settings()
        if settings is not None:
            default_settings.apply_update(settings)

        super().__init__(
            sample_rate=sample_rate,
            keepalive_timeout=40,
            keepalive_interval=20,
            settings=default_settings,
            **kwargs,
        )

        if not api_key or not api_key.strip():
            raise ValueError("60db API key is required and cannot be empty")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._receive_task: asyncio.Task[None] | None = None

    def can_generate_metrics(self) -> bool:
        return True

    async def setup(self, setup) -> None:
        await super().setup(setup)
        await self._connect()

    async def stop(self, frame: EndFrame) -> None:
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame) -> None:
        await super().cancel(frame)
        await self._disconnect()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        if not self._websocket or self._websocket.state is not State.OPEN:
            yield ErrorFrame(error="60db STT connection is unavailable")
            return
        try:
            await self._websocket.send(self._audio_message(audio))
            self._record_stt_audio_usage(audio)
        except Exception as e:
            yield ErrorFrame(error=f"60db STT error: {e}")
            return

        yield None

    async def _send_keepalive(self, silence: bytes) -> None:
        if self._websocket is None:
            return
        await self._websocket.send(self._audio_message(silence))

    def _audio_message(self, audio: bytes) -> str:
        # Raw binary frames select 60db's telephony mu-law mode, while Pipecat
        # supplies signed linear16. The JSON audio frame preserves that format.
        return json.dumps(
            {
                "type": "audio",
                "audio": base64.b64encode(audio).decode("ascii"),
                "encoding": "linear",
                "sample_rate": self.sample_rate,
            }
        )

    def _start_message(self) -> str:
        language = self._settings.language
        languages = None if language in {"", "auto"} else [language]
        message: dict[str, Any] = {
            "type": "start",
            "languages": languages,
            "config": {
                "encoding": "linear",
                "sample_rate": self.sample_rate,
                "audio_enhancement": "on",
                "early_final": False,
                "turn_detector": True,
                "utterance_end_ms": 1200,
                "interim_results_frequency": 500,
            },
        }
        if self._settings.keywords:
            message["keywords"] = self._settings.keywords
        return json.dumps(message)

    async def _connect(self) -> None:
        await self._connect_websocket()
        if self._websocket is None or self._websocket.state is not State.OPEN:
            return
        await super()._connect()
        if not self._receive_task:
            self._receive_task = self.create_task(
                self._receive_task_handler(self._report_error)
            )

    async def _disconnect(self) -> None:
        await super()._disconnect()
        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        await self._disconnect_websocket()

    async def _connect_websocket(self) -> None:
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return

            logger.debug("Connecting to 60db STT")
            url = sixtydb_stt_websocket_url(self._base_url)
            self._websocket = await self._websocket_connect(
                url,
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
            )
            await self._handshake()
            await self._call_event_handler("on_connected")
            logger.debug("Connected to 60db STT")
        except Exception as e:
            await self.push_error(
                error_msg=f"60db STT connection error: {e}", exception=e
            )
            websocket, self._websocket = self._websocket, None
            if websocket:
                try:
                    await websocket.close()
                except Exception as close_error:
                    logger.warning(
                        f"{self} error closing failed handshake: {close_error}"
                    )
            self._websocket = None
            await self._call_event_handler("on_connection_error", str(e))

    async def _handshake(self) -> None:
        if self._websocket is None:
            raise RuntimeError("60db STT WebSocket is not connected")
        async with asyncio.timeout(SIXTYDB_STT_HANDSHAKE_TIMEOUT_SECONDS):
            async for message in self._websocket:
                data = json.loads(message)
                message_type = data.get("type")
                if message_type == "connection_established":
                    break
                if message_type == "error":
                    raise RuntimeError(str(data.get("error") or data))
            else:
                raise RuntimeError("60db STT connection closed during authentication")

            await self._websocket.send(self._start_message())
            async for message in self._websocket:
                data = json.loads(message)
                message_type = data.get("type")
                if message_type == "session_started":
                    return
                if message_type == "error":
                    raise RuntimeError(str(data.get("error") or data))
            else:
                raise RuntimeError("60db STT connection closed before session_started")

    async def _wait_for_session_stopped(self) -> None:
        if self._websocket is None:
            return
        try:
            async with asyncio.timeout(2):
                async for message in self._websocket:
                    if json.loads(message).get("type") == "session_stopped":
                        return
        except TimeoutError:
            pass

    async def _disconnect_websocket(self) -> None:
        websocket = self._websocket
        try:
            if websocket and websocket.state is State.OPEN:
                logger.debug("Disconnecting from 60db STT")
                await websocket.send(json.dumps({"type": "stop"}))
                await self._wait_for_session_stopped()
        except Exception as e:
            logger.error(f"{self} error closing websocket: {e}")
        finally:
            self._websocket = None
            if websocket:
                try:
                    await websocket.close()
                except Exception as close_error:
                    logger.warning(
                        f"{self} error forcing websocket close: {close_error}"
                    )
            await self._call_event_handler("on_disconnected")

    def _get_websocket(self):
        if self._websocket:
            return self._websocket
        raise Exception("Websocket not connected")

    async def _receive_messages(self) -> None:
        # The inherited receive-task handler reconnects when this loop ends.
        async for message in self._get_websocket():
            try:
                await self._process_response(json.loads(message))
            except json.JSONDecodeError:
                logger.warning(f"{self} received non-JSON message: {message}")
            except Exception as e:
                logger.error(f"{self} error processing message: {e}")

    async def _process_response(self, data: dict[str, Any]) -> None:
        message_type = data.get("type")
        if message_type == "error":
            error = data.get("error")
            message = str(error.get("message") if isinstance(error, dict) else error)
            if data.get("terminal"):
                # The turn failed, but the session remains usable for the next
                # caller utterance.
                logger.warning(f"{self} terminal transcription error: {message}")
                await self.push_frame(ErrorFrame(error=message))
                return
            logger.warning(f"{self} rejected message: {message}")
            return

        if message_type != "transcription":
            return

        text = str(data.get("text") or "").strip()
        if not text:
            return

        speech_language = data.get("language")
        if data.get("is_final"):
            await self.emit_stt_usage_metrics()
            await self.push_frame(
                TranscriptionFrame(
                    text,
                    self._user_id,
                    time_now_iso8601(),
                    speech_language,
                    result=data,
                )
            )
        else:
            await self.push_frame(
                InterimTranscriptionFrame(
                    text,
                    self._user_id,
                    time_now_iso8601(),
                    speech_language,
                    result=data,
                )
            )
