"""Telnyx STT WebSocket service.

Subclasses pipecat's WebsocketSTTService to provide streaming STT via the
Telnyx WebSocket API at wss://api.telnyx.com/v2/speech-to-text/transcription.

Protocol:
  - Connect with Authorization: Bearer <key> header.
  - Send raw 16-bit PCM audio as binary WebSocket frames.
  - Receive JSON text frames with transcript, is_final, confidence.
  - Send {"type": "CloseStream"} to end the session gracefully.
"""

import json
from collections.abc import AsyncGenerator
from typing import Any

from loguru import logger
from websockets.asyncio.client import connect as websocket_connect
from websockets.protocol import State

from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.services.stt_service import WebsocketSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601


class TelnyxSTTService(WebsocketSTTService):
    """Telnyx streaming STT over WebSocket.

    Sends raw PCM audio as binary frames, receives JSON transcription results.
    The WebSocket stays open for the session. Supports interim and final
    transcripts depending on the engine.
    """

    def __init__(
        self,
        *,
        api_key: str,
        transcription_engine: str = "Telnyx",
        input_format: str = "linear16",
        sample_rate: int = 16000,
        language: str = "en",
        **kwargs: Any,
    ):
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._api_key = api_key
        self._transcription_engine = transcription_engine
        self._input_format = input_format
        self._sample_rate = sample_rate
        self._language = language
        self._receive_task = None

    def _build_url(self) -> str:
        return (
            "wss://api.telnyx.com/v2/speech-to-text/transcription"
            f"?transcription_engine={self._transcription_engine}"
            f"&input_format={self._input_format}"
            f"&sample_rate={self._sample_rate}"
        )

    async def start(self, frame: Frame):
        await super().start(frame)
        await self._connect()

    async def _connect(self):
        await super()._connect()
        await self._connect_websocket()
        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(
                self._receive_task_handler(self._report_error)
            )

    async def _disconnect(self):
        await super()._disconnect()
        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        await self._disconnect_websocket()

    async def _connect_websocket(self):
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return
            logger.debug("Connecting to Telnyx STT")
            self._websocket = await websocket_connect(
                self._build_url(),
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
            )
            await self._call_event_handler("on_connected")
        except Exception as e:
            await self.push_error(error_msg=f"Unknown error occurred: {e}", exception=e)
            self._websocket = None
            await self._call_event_handler("on_connection_error", f"{e}")

    async def _disconnect_websocket(self):
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                logger.debug("Disconnecting from Telnyx STT")
                await self._websocket.send(json.dumps({"type": "CloseStream"}))
                await self._websocket.close()
        except Exception as e:
            await self.push_error(error_msg=f"Unknown error occurred: {e}", exception=e)
        finally:
            self._websocket = None
            await self._call_event_handler("on_disconnected")

    def _get_websocket(self):
        if self._websocket:
            return self._websocket
        raise Exception("Websocket not connected")

    async def _receive_messages(self):
        async for message in self._get_websocket():
            try:
                msg = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Telnyx STT: non-JSON message: {message}")
                continue

            if "errors" in msg:
                for err in msg["errors"]:
                    logger.error(f"Telnyx STT error: {err.get('detail', err)}")
                await self.push_error(error_msg=str(msg["errors"]))
                continue

            transcript = msg.get("transcript")
            if transcript is None:
                continue

            is_final = msg.get("is_final", False)
            confidence = msg.get("confidence")
            speech_final = msg.get("speech_final", False)
            language = None
            if self._language:
                try:
                    language = Language(self._language)
                except (ValueError, KeyError):
                    pass

            if is_final:
                await self.push_frame(
                    TranscriptionFrame(
                        transcript,
                        self._user_id,
                        time_now_iso8601(),
                        language,
                        result=msg,
                    )
                )
                await self.stop_processing_metrics()
            else:
                await self.push_frame(
                    InterimTranscriptionFrame(
                        transcript,
                        self._user_id,
                        time_now_iso8601(),
                        language,
                        result=msg,
                    )
                )

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        await self.start_processing_metrics()
        if self._websocket and self._websocket.state is State.OPEN:
            try:
                await self._websocket.send(audio)
            except Exception as e:
                logger.warning(f"Telnyx STT: send failed: {e}")
        yield None
