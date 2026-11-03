"""Murf AI WebSocket TTS service.

Vendored from https://github.com/murf-ai/pipecat-murf-tts (v0.2.6, MIT)
and adapted to Dograh's pipecat submodule. Do not add pipecat-murf-tts as a
pip dependency — keep protocol fixes in this file.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, AsyncGenerator, Dict, Literal, Optional, Union

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


class MurfTTSService(WebsocketTTSService):
    """Murf AI WebSocket-based text-to-speech service."""

    class InputParams(BaseModel):
        """Voice and stream settings sent to Murf."""

        voice_id: Optional[str] = "Matthew"
        style: Optional[str] = "Conversational"
        rate: Optional[int] = 0
        pitch: Optional[int] = 0
        pronunciation_dictionary: Optional[Dict[str, Dict[str, str]]] = None
        variation: Optional[int] = 1
        multi_native_locale: Optional[str] = None
        model: Optional[Union[Literal["falcon-2", "FALCON", "GEN2"], str]] = "falcon-2"
        sample_rate: Optional[int] = None
        channel_type: Optional[str] = "MONO"
        format: Optional[str] = "PCM"
        min_buffer_size: Optional[int] = 2
        max_buffer_delay_in_ms: Optional[int] = 0

        @field_validator("voice_id")
        @classmethod
        def validate_voice_id(cls, v: Optional[str]) -> Optional[str]:
            if v is not None and not v.strip():
                raise ValueError("voice_id cannot be empty or whitespace")
            return v

        @field_validator("sample_rate")
        @classmethod
        def validate_sample_rate(cls, v: Optional[int]) -> Optional[int]:
            valid_rates = [8000, 16000, 24000, 44100, 48000]
            if v is not None and v not in valid_rates:
                raise ValueError(f"sample_rate must be one of {valid_rates}, got {v}")
            return v

        @field_validator("rate")
        @classmethod
        def validate_rate(cls, v: Optional[int]) -> Optional[int]:
            if v is not None and not (-50 <= v <= 50):
                raise ValueError(f"rate must be between -50 and 50, got {v}")
            return v

        @field_validator("pitch")
        @classmethod
        def validate_pitch(cls, v: Optional[int]) -> Optional[int]:
            if v is not None and not (-50 <= v <= 50):
                raise ValueError(f"pitch must be between -50 and 50, got {v}")
            return v

        @field_validator("variation")
        @classmethod
        def validate_variation(cls, v: Optional[int]) -> Optional[int]:
            if v is not None and not (0 <= v <= 5):
                raise ValueError(f"variation must be between 0 and 5, got {v}")
            return v

        @field_validator("channel_type")
        @classmethod
        def validate_channel_type(cls, v: Optional[str]) -> Optional[str]:
            valid_types = ["MONO", "STEREO"]
            if v is not None and v not in valid_types:
                raise ValueError(f"channel_type must be one of {valid_types}, got {v}")
            return v

        @field_validator("format")
        @classmethod
        def validate_format(cls, v: Optional[str]) -> Optional[str]:
            valid_formats = ["MP3", "WAV", "FLAC", "ALAW", "ULAW", "PCM", "OGG"]
            if v is not None and v not in valid_formats:
                raise ValueError(f"format must be one of {valid_formats}, got {v}")
            return v

        @field_validator("min_buffer_size")
        @classmethod
        def validate_min_buffer_size(cls, v: Optional[int]) -> Optional[int]:
            # Official plugin documents 40–160. Dograh already aggregates
            # sentences before TTS, so we allow a lower floor (2) to flush
            # immediately instead of waiting for more characters.
            if v is not None and not (2 <= v <= 160):
                raise ValueError(f"min_buffer_size must be between 2 and 160, got {v}")
            return v

        @field_validator("max_buffer_delay_in_ms")
        @classmethod
        def validate_max_buffer_delay_in_ms(cls, v: Optional[int]) -> Optional[int]:
            if v is not None and not (0 <= v <= 1000):
                raise ValueError(
                    f"max_buffer_delay_in_ms must be between 0 and 1000, got {v}"
                )
            return v

    def __init__(
        self,
        *,
        api_key: str,
        url: str = "wss://global.api.murf.ai/v1/speech/stream-input",
        params: Optional[InputParams] = None,
        text_aggregation_mode: Optional[TextAggregationMode] = None,
        **kwargs,
    ):
        params = params or MurfTTSService.InputParams()

        constructor_sample_rate = kwargs.pop("sample_rate", None)
        resolved_sample_rate = (
            params.sample_rate
            if params.sample_rate is not None
            else (
                constructor_sample_rate
                if constructor_sample_rate is not None
                else 24000
            )
        )

        default_settings = TTSSettings(
            model=None,
            voice=params.voice_id or "Matthew",
            language=None,
        )

        super().__init__(
            sample_rate=resolved_sample_rate,
            text_aggregation_mode=text_aggregation_mode,
            push_text_frames=True,
            push_start_frame=True,
            pause_frame_processing=False,
            settings=default_settings,
            **kwargs,
        )

        if not api_key or not api_key.strip():
            raise ValueError("Murf API key is required and cannot be empty")

        self._api_key = api_key
        self._url = url
        self._murf_settings = {
            "style": params.style,
            "rate": params.rate,
            "pitch": params.pitch,
            "pronunciation_dictionary": params.pronunciation_dictionary or {},
            "variation": params.variation,
            "multi_native_locale": params.multi_native_locale,
            "model": params.model,
            "sample_rate": resolved_sample_rate,
            "channel_type": params.channel_type,
            "format": params.format,
            "min_buffer_size": params.min_buffer_size,
            "max_buffer_delay_in_ms": params.max_buffer_delay_in_ms,
        }

        self._receive_task: Optional[asyncio.Task[None]] = None
        self._websocket: Optional[ClientConnection] = None

    def can_generate_metrics(self) -> bool:
        return True

    async def _verify_connection(self) -> bool:
        try:
            if not self._websocket:
                return False
            await self._websocket.ping()
            return True
        except Exception as e:
            logger.error(f"{self} connection verification failed: {e}")
            return False

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        self._murf_settings["sample_rate"] = self.sample_rate
        await self._connect()

    async def stop(self, frame: EndFrame) -> None:
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame) -> None:
        await super().cancel(frame)
        await self._disconnect()

    async def _connect(self):
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

            url = (
                f"{self._url}"
                f"?sample_rate={self._murf_settings['sample_rate']}"
                f"&format={self._murf_settings['format']}"
                f"&channel_type={self._murf_settings['channel_type']}"
                f"&model={self._murf_settings['model']}"
            )

            headers = {"api-key": self._api_key}

            logger.debug("Connecting to Murf")
            self._websocket = await websocket_connect(url, additional_headers=headers)
            await self._send_advanced_settings()
            logger.debug("Connected to Murf")

        except Exception as e:
            logger.error(f"{self} initialization error: {e}")
            self._websocket = None
            await self.push_error(error_msg=f"{self} connection error: {e}", exception=e)

    async def _send_advanced_settings(self) -> None:
        if not self._websocket:
            return

        advanced_settings: Dict[str, Any] = {}
        if self._murf_settings.get("min_buffer_size") is not None:
            advanced_settings["min_buffer_size"] = self._murf_settings["min_buffer_size"]
        if self._murf_settings.get("max_buffer_delay_in_ms") is not None:
            advanced_settings["max_buffer_delay_in_ms"] = self._murf_settings[
                "max_buffer_delay_in_ms"
            ]

        if not advanced_settings:
            return

        try:
            await self._websocket.send(json.dumps(advanced_settings))
            logger.debug(f"{self} sent advanced settings: {advanced_settings}")
        except Exception as e:
            logger.error(f"{self} error sending advanced settings: {e}")

    async def _disconnect_websocket(self) -> None:
        try:
            await self.stop_all_metrics()

            if self._websocket:
                logger.debug("Disconnecting from Murf")
                await self._websocket.close()
        except Exception as e:
            logger.error(f"{self} error closing websocket: {e}")
        finally:
            await self.remove_active_audio_context()
            self._websocket = None

    def _get_websocket(self) -> ClientConnection:
        if self._websocket:
            return self._websocket
        raise Exception("Websocket not connected")

    async def flush_audio(self, context_id: Optional[str] = None):
        flush_id = context_id or self.get_active_audio_context_id()
        if not flush_id or not self._websocket:
            return

        logger.debug(f"{self}: flushing audio and finalizing turn")
        try:
            end_msg = {"context_id": flush_id, "end": True}
            await self._websocket.send(json.dumps(end_msg))
            logger.debug(f"{self} marked turn complete for context {flush_id}")
        except Exception as e:
            logger.error(f"{self} error flushing audio: {e}")

    async def on_audio_context_interrupted(self, context_id: str):
        await self.stop_all_metrics()
        if context_id and self._websocket:
            try:
                clear_msg = json.dumps({"clear": True, "context_id": context_id})
                await self._websocket.send(clear_msg)
                logger.debug(f"{self} cleared context {context_id}")
            except Exception as e:
                logger.error(f"{self} error cancelling context: {e}")
        await super().on_audio_context_interrupted(context_id)

    async def _process_messages(self) -> None:
        async for message in self._get_websocket():
            try:
                if isinstance(message, str):
                    data = json.loads(message)
                    await self._process_json_message(data)
                else:
                    logger.warning(
                        f"{self} received unexpected non-string message: {type(message)}"
                    )
            except Exception as e:
                logger.error(f"{self} error processing message: {e}")
                await self.push_error(
                    error_msg=f"{self} error processing message: {e}", exception=e
                )

    async def _receive_messages(self) -> None:
        while True:
            await self._process_messages()
            logger.debug(f"{self} websocket connection ended, reconnecting")
            await self._connect_websocket()

    async def _process_json_message(self, data: Dict[str, Any]) -> None:
        received_ctx_id = data.get("context_id")

        if not received_ctx_id or not isinstance(received_ctx_id, str):
            logger.warning(f"Missing or invalid context_id in message: {data}")
            return

        if not self.audio_context_available(received_ctx_id):
            return

        if "error" in data:
            error_msg = f"{self} error: {data['error']}"
            logger.error(error_msg)
            await self.stop_all_metrics()
            await self.append_to_audio_context(
                received_ctx_id, TTSStoppedFrame(context_id=received_ctx_id)
            )
            await self.remove_audio_context(received_ctx_id)
            await self.push_error(error_msg=error_msg)
            return

        if "audio" in data:
            try:
                audio_data = base64.b64decode(data["audio"])
                await self._process_audio_data_to_context(received_ctx_id, audio_data)
            except Exception as e:
                logger.error(f"{self} error decoding audio data: {e}")
            return

        if data.get("final") is True:
            logger.debug(f"{self} received final output for context {received_ctx_id}")
            await self.stop_ttfb_metrics()
            await self.append_to_audio_context(
                received_ctx_id, TTSStoppedFrame(context_id=received_ctx_id)
            )
            await self.remove_audio_context(received_ctx_id)
            return

        logger.debug(f"{self} received unknown message: {data}")

    async def _process_audio_data_to_context(
        self, context_id: str, audio_data: bytes
    ) -> None:
        num_channels = 2 if self._murf_settings["channel_type"] == "STEREO" else 1
        frame = TTSAudioRawFrame(
            audio=audio_data,
            sample_rate=self.sample_rate,
            num_channels=num_channels,
            context_id=context_id,
        )
        await self.append_to_audio_context(context_id, frame)

    def _build_voice_config_message(
        self, text: str, context_id: str, is_last: bool = False
    ) -> Dict[str, Any]:
        voice_config: Dict[str, Any] = {
            "voice_id": self._settings.voice,
            "style": self._murf_settings["style"],
            "rate": self._murf_settings["rate"],
            "pitch": self._murf_settings["pitch"],
            "pronunciation_dictionary": self._murf_settings["pronunciation_dictionary"],
            "variation": self._murf_settings["variation"],
        }

        if self._murf_settings["multi_native_locale"]:
            voice_config["multi_native_locale"] = self._murf_settings[
                "multi_native_locale"
            ]

        message: Dict[str, Any] = {
            "voice_config": voice_config,
            "context_id": context_id,
            "text": text,
            "end": is_last,
        }

        if self._murf_settings.get("min_buffer_size") is not None:
            message["min_buffer_size"] = self._murf_settings["min_buffer_size"]
        if self._murf_settings.get("max_buffer_delay_in_ms") is not None:
            message["max_buffer_delay_in_ms"] = self._murf_settings[
                "max_buffer_delay_in_ms"
            ]

        logger.debug(f"{self} voice config message: {message}")
        return message

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        logger.debug(f"{self}: Generating TTS [{text}]")

        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()

            voice_config_msg = self._build_voice_config_message(
                text, context_id=context_id, is_last=False
            )

            try:
                await self._get_websocket().send(json.dumps(voice_config_msg))
                await self.start_tts_usage_metrics(text)
            except Exception as e:
                yield ErrorFrame(error=f"Error sending message: {e}")
                yield TTSStoppedFrame(context_id=context_id)
                await self._disconnect()
                await self._connect()
                return

            return
        except Exception as e:
            yield ErrorFrame(error=f"Unknown error occurred: {e}")
