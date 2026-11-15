"""60db streaming text-to-speech service."""

from __future__ import annotations

import asyncio
import base64
import json
from collections import deque
from typing import AsyncGenerator, Optional
from urllib.parse import urlparse, urlunparse

from loguru import logger
from pydantic import BaseModel, field_validator

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TextAggregationMode, WebsocketTTSService
from pipecat.utils.tracing.service_decorators import traced_tts

try:
    from websockets.asyncio.client import ClientConnection
    from websockets.asyncio.client import connect as websocket_connect
    from websockets.protocol import State
except ModuleNotFoundError as e:
    logger.error(f"Exception: {e}")
    raise Exception(f"Missing module: {e}") from e


def sixtydb_tts_websocket_url(base_url: str) -> str:
    """Build the authenticated 60db TTS WebSocket URL."""
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    scheme = {"https": "wss", "http": "ws"}.get(parsed.scheme, parsed.scheme)
    if scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError("Invalid 60db base URL")
    return urlunparse((scheme, parsed.netloc, "/ws/tts", "", "", ""))


class SixtydbTTSService(WebsocketTTSService):
    """60db Kaak text-to-speech over its bidirectional WebSocket API."""

    class InputParams(BaseModel):
        voice_id: str = "84982e79-c596-4883-b6fa-9af334767aef"
        speed: float = 1.0
        stability: int = 50
        similarity: int = 75

        @field_validator("voice_id")
        @classmethod
        def validate_voice_id(cls, value: str) -> str:
            if not value.strip():
                raise ValueError("voice_id cannot be empty")
            return value

        @field_validator("speed")
        @classmethod
        def validate_speed(cls, value: float) -> float:
            if not 0.5 <= value <= 2.0:
                raise ValueError("speed must be between 0.5 and 2.0")
            return value

        @field_validator("stability", "similarity")
        @classmethod
        def validate_percent(cls, value: int) -> int:
            if not 0 <= value <= 100:
                raise ValueError("value must be between 0 and 100")
            return value

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.60db.ai",
        params: Optional[InputParams] = None,
        text_aggregation_mode: Optional[TextAggregationMode] = None,
        **kwargs,
    ):
        params = params or self.InputParams()
        constructor_sample_rate = kwargs.pop("sample_rate", None)
        resolved_sample_rate = constructor_sample_rate or 16000

        super().__init__(
            sample_rate=resolved_sample_rate,
            text_aggregation_mode=text_aggregation_mode,
            push_text_frames=True,
            push_start_frame=True,
            pause_frame_processing=False,
            settings=TTSSettings(
                model=None,
                voice=params.voice_id,
                language=None,
            ),
            **kwargs,
        )

        if not api_key or not api_key.strip():
            raise ValueError("60db API key is required and cannot be empty")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._params = params
        self._waiting_syntheses: deque[tuple[str, str]] = deque()
        self._active_context_id: str | None = None
        self._active_text: str | None = None
        self._receive_task: Optional[asyncio.Task[None]] = None
        self._websocket: Optional[ClientConnection] = None

    def can_generate_metrics(self) -> bool:
        return True

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self._connect()

    async def stop(self, frame: EndFrame) -> None:
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame) -> None:
        await super().cancel(frame)
        await self._disconnect()

    async def _connect(self) -> None:
        await super()._connect()
        await self._connect_websocket()
        if self._websocket and (
            self._receive_task is None or self._receive_task.done()
        ):
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

            url = sixtydb_tts_websocket_url(self._base_url)
            logger.debug("Connecting to 60db TTS")
            self._websocket = await websocket_connect(
                url, additional_headers={"Authorization": f"Bearer {self._api_key}"}
            )
            await self._wait_for_connection_established()
            logger.debug("Connected to 60db TTS")
        except Exception as e:
            logger.error(f"{self} initialization error: {e}")
            websocket, self._websocket = self._websocket, None
            if websocket:
                try:
                    await websocket.close()
                except Exception as close_error:
                    logger.warning(
                        f"{self} error closing failed handshake: {close_error}"
                    )
            await self.push_error(
                error_msg=f"{self} connection error: {e}", exception=e
            )

    async def _wait_for_connection_established(self) -> None:
        if self._websocket is None:
            raise RuntimeError("60db TTS WebSocket is not connected")
        async for message in self._websocket:
            data = json.loads(message)
            if (
                data.get("type") == "connection_established"
                or "connection_established" in data
            ):
                return
            if data.get("type") == "error" or "error" in data:
                raise RuntimeError(self._error_message(data))
        raise RuntimeError("60db TTS connection closed during authentication")

    async def _disconnect_websocket(self) -> None:
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                logger.debug("Disconnecting from 60db TTS")
                await self._websocket.close()
        except Exception as e:
            logger.error(f"{self} error closing websocket: {e}")
        finally:
            self._websocket = None

    def _get_websocket(self) -> ClientConnection:
        if self._websocket:
            return self._websocket
        raise Exception("Websocket not connected")

    async def flush_audio(self, context_id: Optional[str] = None) -> None:
        # Every run_tts turn is flushed immediately; 60db keeps each synthesis
        # isolated by its own context ID.
        return

    async def on_audio_context_interrupted(self, context_id: str) -> None:
        if self._websocket and context_id:
            try:
                await self._websocket.send(
                    json.dumps({"close_context": {"context_id": context_id}})
                )
            except Exception as e:
                logger.warning(f"{self} error cancelling context: {e}")
        await super().on_audio_context_interrupted(context_id)

    async def _start_next_context(self) -> None:
        """Send one create_context request, keeping provider turns serial.

        Live 60db acknowledgement, media, and completion messages do not carry
        a context ID. Sending one synthesis at a time makes that untagged
        stream unambiguous without guessing from arrival order.
        """
        if self._active_context_id or not self._waiting_syntheses:
            return
        if not self._websocket:
            return

        context_id, text = self._waiting_syntheses.popleft()
        self._active_context_id = context_id
        self._active_text = text
        await self._websocket.send(
            json.dumps(
                {
                    "create_context": {
                        "context_id": context_id,
                        "voice_id": self._params.voice_id,
                        "audio_config": {
                            "audio_encoding": "LINEAR16",
                            "sample_rate_hertz": self.sample_rate,
                        },
                        "speed": self._params.speed,
                        "stability": self._params.stability,
                        "similarity": self._params.similarity,
                    }
                }
            )
        )

    async def _finish_active_context(self, context_id: str) -> None:
        if self.audio_context_available(context_id):
            await self.append_to_audio_context(
                context_id, TTSStoppedFrame(context_id=context_id)
            )
            await self.remove_audio_context(context_id)
        if context_id == self._active_context_id:
            self._active_context_id = None
            self._active_text = None
        await self._start_next_context()

    async def _process_messages(self) -> None:
        async for message in self._get_websocket():
            try:
                await self._process_json_message(json.loads(message))
            except Exception as e:
                logger.error(f"{self} error processing message: {e}")
                await self.push_error(
                    error_msg=f"{self} error processing message: {e}", exception=e
                )

    async def _receive_messages(self) -> None:
        await self._process_messages()

    @staticmethod
    def _error_message(data: dict) -> str:
        error = data.get("error", "60db TTS reported an unknown error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error)

    @staticmethod
    def _audio_bytes(data: dict) -> bytes:
        chunk = data.get("audio_chunk") or {}
        encoded = chunk.get("audioContent") or chunk.get("audio_content")
        if not encoded:
            raise ValueError("60db audio chunk has no audio content")
        return base64.b64decode(encoded)

    async def _process_json_message(self, data: dict) -> None:
        if data.get("type") == "error" or "error" in data:
            error_msg = self._error_message(data)
            logger.error(f"{self} error: {error_msg}")
            await self.stop_all_metrics()
            context_id = data.get("context_id") or self._active_context_id
            if context_id:
                await self._finish_active_context(context_id)
            await self.push_error(error_msg=error_msg)
            return

        if data.get("type") == "context_created" or "context_created" in data:
            created = data.get("context_created")
            created_context_id: str | None = data.get("context_id") or (
                created.get("context_id") if isinstance(created, dict) else None
            )
            if not created_context_id:
                created_context_id = self._active_context_id
            if (
                not created_context_id
                or created_context_id != self._active_context_id
                or not self._active_text
                or not self._websocket
            ):
                return
            try:
                await self._websocket.send(
                    json.dumps(
                        {
                            "send_text": {
                                "context_id": created_context_id,
                                "text": self._active_text,
                            }
                        }
                    )
                )
                await self._websocket.send(
                    json.dumps({"flush_context": {"context_id": created_context_id}})
                )
            except Exception as e:
                logger.error(f"{self} error sending synthesis request: {e}")
                if created_context_id:
                    await self._finish_active_context(created_context_id)
                await self.push_error(error_msg=str(e), exception=e)
            return

        if "audio_chunk" in data:
            chunk = data.get("audio_chunk") or {}
            context_id = (
                data.get("context_id") or chunk.get("context_id")
                if isinstance(chunk, dict)
                else data.get("context_id")
            ) or self._active_context_id
            if not context_id or not self.audio_context_available(context_id):
                return
            try:
                audio = self._audio_bytes(data)
                await self.append_to_audio_context(
                    context_id,
                    TTSAudioRawFrame(
                        audio=audio,
                        sample_rate=self.sample_rate,
                        num_channels=1,
                        context_id=context_id,
                    ),
                )
            except Exception as e:
                logger.error(f"{self} error decoding audio data: {e}")
            return

        completed = data.get("flush_completed")
        if completed is not None:
            context_id = (
                data.get("context_id")
                or (
                    completed.get("context_id") if isinstance(completed, dict) else None
                )
                or self._active_context_id
            )
            if not context_id or not self.audio_context_available(context_id):
                return
            await self.stop_ttfb_metrics()
            await self._finish_active_context(context_id)

        if data.get("type") == "context_closed" or "context_closed" in data:
            closed = data.get("context_closed")
            context_id = data.get("context_id") or (
                closed.get("context_id") if isinstance(closed, dict) else None
            )
            if not context_id:
                context_id = self._active_context_id
            if context_id and context_id == self._active_context_id:
                self._active_context_id = None
                self._active_text = None
                await self._start_next_context()

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        logger.debug(f"{self}: Generating TTS for context {context_id}")
        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()

            self._waiting_syntheses.append((context_id, text))
            try:
                await self._start_next_context()
                await self.start_tts_usage_metrics(text)
            except Exception as e:
                if self._active_context_id:
                    self._active_context_id = None
                    self._active_text = None
                yield ErrorFrame(error=f"Error sending message: {e}")
                yield TTSStoppedFrame(context_id=context_id)
                await self._disconnect()
                await self._connect()
                await self._start_next_context()
                return
        except Exception as e:
            yield ErrorFrame(error=f"Unknown error occurred: {e}")
