"""Dograh's Live Translate service for Gemini's `gemini-3.5-live-translate-preview` model.

Implemented as a direct WebSocket client rather than via :mod:`google.genai`
because pipecat pins ``google-genai<2.0`` while ``translation_config`` only
ships in ``google-genai>=2.8``.  When pipecat unpins, this module can be
collapsed into a thin subclass of pipecat's :class:`GeminiLiveLLMService`
once that service grows ``translation_config`` support.

Translate-only model — explicitly unsupported, with hard runtime errors:
  * tools / function calling
  * system instructions
  * mid-call settings or context changes (target_language_code, model, etc.)

Reference: https://ai.google.dev/gemini-api/docs/live-api/live-translate
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from pipecat.frames.frames import (
    AggregationType,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMSetToolChoiceFrame,
    LLMSetToolsFrame,
    LLMTextFrame,
    LLMUpdateSettingsFrame,
    StartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallFromLLM, LLMService
from pipecat.utils.string import match_endofsentence
from pipecat.utils.time import time_now_iso8601

from api.services.pipecat.exceptions import UnsupportedRealtimeFeatureError

# Mirrors pipecat's GeminiLiveLLMService reconnect policy
# (pipecat/src/pipecat/services/google/gemini_live/llm.py:112-113).
MAX_CONSECUTIVE_FAILURES = 3
CONNECTION_ESTABLISHED_THRESHOLD = 10.0  # seconds

# URI template per google-genai==2.8.0 (.genai28_ref/live.py:981). The
# `api_version` segment is configurable via Settings; everything else is
# fixed by the Live Translate wire protocol.
_WS_URI_TEMPLATE = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.{api_version}.GenerativeService.BidiGenerateContent"
)

# Gemini Live emits PCM at 24kHz mono (pipecat/src/pipecat/services/google/
# gemini_live/llm.py:539). Live Translate inherits the same audio shape.
_DEFAULT_OUTPUT_SAMPLE_RATE = 24000


class DograhGeminiLiveTranslateLLMSettings(BaseModel):
    """Settings for :class:`DograhGeminiLiveTranslateLLMService`.

    Plain Pydantic model — does not participate in pipecat's NOT_GIVEN delta
    machinery because mid-call updates are unsupported by the model and
    rejected at the :meth:`_update_settings` boundary.
    """

    model: str = Field(default="gemini-3.5-live-translate-preview")
    target_language_code: str = Field(default="en")
    echo_target_language: bool = Field(default=False)
    api_version: str = Field(default="v1beta")

    def validate_complete(self) -> None:
        """No-op shim for :meth:`pipecat.services.ai_service.AIService.start`.

        ``AIService.start`` calls ``self._settings.validate_complete()`` to
        catch ``NOT_GIVEN`` fields on services that use pipecat's
        ``ServiceSettings`` delta machinery. This service intentionally uses
        a plain Pydantic model (mid-call updates are unsupported), so there
        is nothing to validate.
        """
        return None


class DograhGeminiLiveTranslateLLMService(LLMService):
    """Gemini Live Translate (preview) — manual WebSocket implementation.

    See module docstring for SDK-bypass rationale and the list of features
    this service explicitly rejects at runtime.
    """

    Settings = DograhGeminiLiveTranslateLLMSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str,
        settings: Settings | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._api_key = api_key
        self._settings = settings or self.Settings()
        self._websocket: Any | None = None
        self._receive_task: Any | None = None
        # Reconnect bookkeeping — mirrors GeminiLiveLLMService
        # (pipecat/src/pipecat/services/google/gemini_live/llm.py:549).
        self._consecutive_failures: int = 0
        self._connection_start_time: float | None = None
        self._disconnecting: bool = False
        # Bot-turn lifecycle — driven by serverContent.modelTurn arrivals
        # and turn_complete / interrupted signals.
        self._bot_is_responding: bool = False
        # User-side transcription aggregation — Gemini Live emits the
        # source-language transcript in word/phrase fragments. Buffer
        # until an end-of-sentence marker or a short idle timeout. Mirrors
        # GeminiLiveLLMService (gemini_live/llm.py:1874-1924).
        self._user_transcription_buffer: str = ""
        self._transcription_timeout_task: Any | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._connect()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._disconnect()

    def _build_setup_message(self) -> dict:
        """Construct the BidiGenerateContent setup payload.

        Wire format mirrors what the google-genai>=2.8 SDK actually sends
        over the wire (verified by serializing a ``LiveConnectConfig``
        through ``_live_converters._LiveConnectParameters_to_mldev`` —
        see ``.genai28_ref/_live_converters.py``). The shape is
        **mixed-case**: top-level ``setup`` keys and direct
        ``generationConfig`` children (``responseModalities``,
        ``speechConfig``, ``translationConfig``) are camelCase, but
        everything nested inside ``speechConfig`` and ``translationConfig``
        is snake_case — those subtrees are passed through unchanged from
        ``model_dump(exclude_none=True)``.

        ``inputAudioTranscription`` and ``outputAudioTranscription`` are
        siblings of ``generationConfig`` on the ``setup`` object. Moving
        them inside ``generationConfig`` causes the server to reject the
        whole setup with a 1007 close frame (empirically verified).

        ``speechConfig`` is included with a default ``"Puck"`` voice even
        though the docs say the translate-preview model auto-clones the
        input speaker's voice. Empirically the server stops emitting any
        ``modelTurn`` audio or ``outputTranscription`` when
        ``speechConfig`` is absent, so we keep the block — its
        ``voice_name`` is then ignored by the model per the "Voice
        Replication" entry in the docs' Limitations section.

        TODO: When pipecat unpins google-genai<2, replace this manual
        construction with
        ``LiveConnectConfig(translation_config=TranslationConfig(target_language_code=...))``.
        See ``google-genai>=2.8.0`` ``types.TranslationConfig``.
        """
        translation_config: dict[str, Any] = {
            "target_language_code": self._settings.target_language_code,
        }
        if self._settings.echo_target_language:
            translation_config["echo_target_language"] = True
        return {
            "setup": {
                # ``models/`` prefix added per .genai28_ref/live.py:995 (t.t_model).
                "model": f"models/{self._settings.model}",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "translationConfig": translation_config,
                    "speechConfig": {
                        "voice_config": {
                            "prebuilt_voice_config": {
                                "voice_name": "Puck",
                            },
                        },
                    },
                },
                # Bot-side translated transcripts.
                "outputAudioTranscription": {},
                # Source-side transcripts so the dashboard call log shows
                # what the user said alongside the translated bot output.
                "inputAudioTranscription": {},
            }
        }

    async def _connect(self):
        """Open the Live Translate WebSocket and complete the setup handshake."""
        if self._websocket is not None:
            return
        self._disconnecting = False
        uri = _WS_URI_TEMPLATE.format(api_version=self._settings.api_version)
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }
        logger.debug(f"{self}: connecting to Live Translate at {uri}")
        self._websocket = await ws_connect(uri, additional_headers=headers)

        setup_message = self._build_setup_message()
        logger.info(
            f"{self}: sending setup model={self._settings.model!r} "
            f"target_language_code={self._settings.target_language_code!r} "
            f"echo_target_language={self._settings.echo_target_language}"
        )
        await self._websocket.send(json.dumps(setup_message))

        # Handshake: first server message must contain setupComplete.
        raw = await self._websocket.recv()
        response = json.loads(raw)
        if "setupComplete" not in response:
            raise RuntimeError(
                f"Live Translate handshake failed; expected setupComplete, "
                f"got keys={list(response.keys())}"
            )
        logger.info(f"{self}: connected to Live Translate")
        self._connection_start_time = time.time()

        self._receive_task = self.create_task(
            self._receive_task_handler(),
            name="gemini-live-translate-recv",
        )

    async def _disconnect(self):
        """Close the WebSocket and cancel the receive task. Idempotent."""
        if self._disconnecting:
            return
        self._disconnecting = True
        logger.debug(f"{self}: disconnecting from Live Translate")
        if self._transcription_timeout_task is not None:
            await self.cancel_task(self._transcription_timeout_task, timeout=0.5)
            self._transcription_timeout_task = None
        self._user_transcription_buffer = ""
        if self._receive_task is not None:
            await self.cancel_task(self._receive_task, timeout=1.0)
            self._receive_task = None
        if self._websocket is not None:
            try:
                await self._websocket.close()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"{self}: ws.close() raised (ignored): {e}")
            self._websocket = None
        self._connection_start_time = None

    async def _reconnect(self):
        """Tear down and re-establish the WebSocket. Mirrors upstream — no backoff."""
        await self._disconnect()
        await self._connect()

    def _check_and_reset_failure_counter(self):
        """Reset the failure counter once a connection has been stable.

        Mirrors :class:`GeminiLiveLLMService._check_and_reset_failure_counter`
        (pipecat/src/pipecat/services/google/gemini_live/llm.py:1300-1315).
        """
        if (
            self._connection_start_time
            and self._consecutive_failures > 0
            and time.time() - self._connection_start_time
            >= CONNECTION_ESTABLISHED_THRESHOLD
        ):
            logger.info(
                f"{self}: connection stable for {CONNECTION_ESTABLISHED_THRESHOLD}s, "
                f"resetting failure counter from {self._consecutive_failures} to 0"
            )
            self._consecutive_failures = 0

    async def _handle_connection_error(self, error: Exception) -> bool:
        """Track a failure and decide whether to reconnect.

        Returns True to retry, False if the failure budget is exhausted (in
        which case an :class:`ErrorFrame` is pushed via :meth:`push_error`).
        """
        self._consecutive_failures += 1
        logger.warning(
            f"{self}: connection error "
            f"({self._consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}): {error}"
        )
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            await self.push_error(
                error_msg=(
                    f"Gemini Live Translate connection failed after "
                    f"{MAX_CONSECUTIVE_FAILURES} consecutive attempts: {error}"
                ),
                exception=error,
            )
            return False
        logger.info(
            f"{self}: attempting reconnection "
            f"({self._consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})"
        )
        return True

    async def _receive_task_handler(self):
        """Read server messages until the WebSocket closes or fatally errors."""
        try:
            async for raw in self._websocket:
                self._check_and_reset_failure_counter()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(f"{self}: dropping malformed server message")
                    continue
                await self._handle_server_message(message)
        except ConnectionClosed as e:
            if self._disconnecting:
                return
            should_reconnect = await self._handle_connection_error(e)
            if should_reconnect:
                await self._reconnect()
        except Exception as e:  # noqa: BLE001
            if self._disconnecting:
                return
            should_reconnect = await self._handle_connection_error(e)
            if should_reconnect:
                await self._reconnect()

    async def _handle_server_message(self, message: dict):
        """Dispatch a decoded server message to the appropriate handler.

        Mirrors the dispatch order in
        pipecat/src/pipecat/services/google/gemini_live/llm.py:1242-1280,
        collapsed for the translate surface (no grounding metadata, no
        session resumption, no tool calls).
        """
        if message.get("setupComplete") is not None:
            # Already consumed during _connect() handshake; defensive no-op.
            return

        if "toolCall" in message:
            # Translate does not declare tools; surfacing one here means
            # the model contract has changed underneath us.
            raise UnsupportedRealtimeFeatureError(
                "Live Translate model unexpectedly emitted a toolCall message."
            )

        if message.get("goAway"):
            # Upstream pipecat does not eagerly disconnect on goAway; log
            # and let the natural close drive the reconnect path.
            logger.warning(f"{self}: server sent goAway: {message['goAway']}")

        server_content = message.get("serverContent")
        if not server_content:
            # Surface anything unexpected (e.g. usageMetadata, error frames)
            # so we can see what the server is actually emitting when
            # responses don't materialize.
            logger.debug(
                f"{self}: non-serverContent message keys={list(message.keys())}"
            )
            return
        logger.debug(
            f"{self}: serverContent keys={list(server_content.keys())}"
        )

        if server_content.get("interrupted"):
            logger.debug(f"{self}: server signaled interruption")
            await self.broadcast_interruption()
            if self._bot_is_responding:
                self._bot_is_responding = False
                await self.push_frame(BotStoppedSpeakingFrame())

        if "inputTranscription" in server_content:
            await self._handle_msg_input_transcription(
                server_content["inputTranscription"]
            )

        if "modelTurn" in server_content:
            await self._handle_msg_model_turn(server_content["modelTurn"])

        if "outputTranscription" in server_content:
            await self._handle_msg_output_transcription(
                server_content["outputTranscription"]
            )

        if server_content.get("turnComplete"):
            await self._handle_msg_turn_complete()

    async def _handle_msg_model_turn(self, model_turn: dict):
        """Emit a TTSAudioRawFrame for each inline audio part.

        Live Translate is configured with ``responseModalities=["AUDIO"]``
        so only ``inlineData`` parts with an ``audio/pcm`` mime type are
        expected; anything else is logged and skipped.
        """
        for part in model_turn.get("parts") or []:
            inline = part.get("inlineData")
            if not inline:
                continue
            mime_type = inline.get("mimeType", "")
            if not mime_type.startswith("audio/pcm"):
                logger.warning(f"{self}: unexpected inlineData mime_type={mime_type!r}")
                continue
            data_b64 = inline.get("data")
            if not data_b64:
                continue
            try:
                audio = base64.b64decode(data_b64)
            except (ValueError, TypeError) as e:
                logger.warning(f"{self}: failed to decode audio chunk: {e}")
                continue
            if not self._bot_is_responding:
                self._bot_is_responding = True
                await self.push_frame(TTSStartedFrame())
                await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(
                TTSAudioRawFrame(
                    audio=audio,
                    sample_rate=_DEFAULT_OUTPUT_SAMPLE_RATE,
                    num_channels=1,
                )
            )

    async def _handle_msg_output_transcription(self, output: dict):
        """Emit bot-side text frames for the translated output.

        Mirrors ``GeminiLiveLLMService._push_output_transcription_text_frames``
        (pipecat/src/pipecat/services/google/gemini_live/llm.py:1961-1978):
        push an ``LLMTextFrame`` (consumed by RTVI for the "bot-llm-text"
        event, marked ``append_to_context=False`` to avoid context
        duplication) followed by a ``TTSTextFrame`` aggregated by
        sentence (picked up by the BaseOutputTransport with audio-clock
        timing and surfaced as a bot message by
        :class:`RealtimeFeedbackObserver`).

        We deliberately do **not** push ``TranscriptionFrame`` here — the
        dograh observer treats every ``TranscriptionFrame`` as a user
        message regardless of ``user_id``, so the translated text would
        appear on the user side of the chat.
        """
        text = output.get("text")
        if not text:
            return
        # On Vertex paths output_transcription can arrive before any
        # modelTurn audio — bracket the bot turn here too, matching
        # gemini_live/llm.py:1954-1957.
        if not self._bot_is_responding:
            self._bot_is_responding = True
            await self.push_frame(TTSStartedFrame())
            await self.push_frame(LLMFullResponseStartFrame())
        llm_text_frame = LLMTextFrame(text)
        llm_text_frame.append_to_context = False
        await self.push_frame(llm_text_frame)
        tts_text_frame = TTSTextFrame(text, aggregated_by=AggregationType.SENTENCE)
        tts_text_frame.includes_inter_frame_spaces = True
        await self.push_frame(tts_text_frame)

    async def _handle_msg_input_transcription(self, input_transcription: dict):
        """Aggregate source-language user transcription fragments into sentences.

        Gemini Live emits the input transcript in word/short-phrase chunks.
        Buffer until an end-of-sentence marker or a short idle timeout,
        then push one finalized :class:`TranscriptionFrame` per sentence.
        Mirrors ``GeminiLiveLLMService._handle_msg_input_transcription``
        (gemini_live/llm.py:1874-1924).
        """
        text = input_transcription.get("text")
        if not text:
            return

        if self._transcription_timeout_task is not None:
            await self.cancel_task(self._transcription_timeout_task)
            self._transcription_timeout_task = None

        if text.startswith(" ") and not self._user_transcription_buffer:
            text = text.lstrip()

        self._user_transcription_buffer += text

        while True:
            eos_end_marker = match_endofsentence(self._user_transcription_buffer)
            if not eos_end_marker:
                break
            complete_sentence = self._user_transcription_buffer[:eos_end_marker]
            self._user_transcription_buffer = self._user_transcription_buffer[
                eos_end_marker:
            ]
            await self._push_user_transcription(complete_sentence)

        if self._user_transcription_buffer:
            self._transcription_timeout_task = self.create_task(
                self._transcription_timeout_handler(),
                name="gemini-live-translate-user-transcription-flush",
            )
            # Let the event loop schedule the task before it gets cancelled.
            await asyncio.sleep(0)

    async def _push_user_transcription(self, text: str):
        """Push a finalized user :class:`TranscriptionFrame` upstream.

        Upstream direction matches pipecat's GeminiLive
        (gemini_live/llm.py:1839-1847) so the user context aggregator
        picks it up.
        """
        await self.push_frame(
            TranscriptionFrame(
                text=text,
                user_id="",
                timestamp=time_now_iso8601(),
                finalized=True,
            ),
            FrameDirection.UPSTREAM,
        )

    async def _transcription_timeout_handler(self):
        """Flush a partial user transcription buffer after a short idle."""
        try:
            await asyncio.sleep(0.5)
            if self._user_transcription_buffer:
                complete_sentence = self._user_transcription_buffer
                self._user_transcription_buffer = ""
                await self._push_user_transcription(complete_sentence)
        except asyncio.CancelledError:
            raise

    async def _handle_msg_turn_complete(self):
        """Close the bot turn (AUDIO modality only)."""
        if not self._bot_is_responding:
            return
        self._bot_is_responding = False
        await self.push_frame(TTSStoppedFrame())
        await self.push_frame(LLMFullResponseEndFrame())

    async def _send_user_audio(self, frame: InputAudioRawFrame):
        """Forward an inbound audio chunk to the Live Translate WebSocket.

        Wire format per the Live Translate docs
        (https://ai.google.dev/gemini-api/docs/live-api/live-translate#sending_audio):
        ``realtimeInput.audio = {mimeType, data(base64)}`` (single object,
        not the legacy ``mediaChunks`` list). The translate model expects
        raw 16-bit little-endian PCM mono at 16 kHz.

        Send failures are logged; the receive task drives reconnect.
        """
        if self._disconnecting or self._websocket is None:
            return
        payload = {
            "realtimeInput": {
                "audio": {
                    "mimeType": f"audio/pcm;rate={frame.sample_rate}",
                    "data": base64.b64encode(frame.audio).decode("ascii"),
                }
            }
        }
        try:
            await self._websocket.send(json.dumps(payload))
        except Exception as e:  # noqa: BLE001
            if not self._disconnecting:
                logger.warning(f"{self}: failed to send audio chunk: {e}")

    # ------------------------------------------------------------------
    # Frame ingress — reject unsupported features at the boundary.
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, LLMSetToolsFrame):
            raise UnsupportedRealtimeFeatureError(
                "Live Translate model does not support tools / function calling."
            )
        if isinstance(frame, LLMSetToolChoiceFrame):
            raise UnsupportedRealtimeFeatureError(
                "Live Translate model does not support tools / function calling."
            )
        if isinstance(frame, LLMUpdateSettingsFrame):
            raise UnsupportedRealtimeFeatureError(
                "Live Translate model does not support mid-call settings updates "
                "(e.g. target_language_code, model). Restart the call to change."
            )
        if isinstance(frame, LLMMessagesAppendFrame):
            raise UnsupportedRealtimeFeatureError(
                "Live Translate model has no conversational context; "
                "LLMMessagesAppendFrame is not supported."
            )

        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            await self._send_user_audio(frame)
            await self.push_frame(frame, direction)
        elif isinstance(frame, LLMContextFrame):
            # Translate has no conversational context, but a downstream
            # workflow may still emit one. Validate (rejects system
            # instructions) and drop silently.
            await self._handle_context(frame.context)
        else:
            # Forward every other frame (StartFrame, EndFrame, CancelFrame,
            # UserStartedSpeakingFrame, BotStoppedSpeakingFrame, etc.) so
            # downstream processors can transition out of their pre-start
            # state. Mirrors GeminiLiveLLMService.process_frame (see
            # pipecat/services/google/gemini_live/llm.py:812).
            await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    # Defense-in-depth overrides — programmatic paths that bypass frames.
    # ------------------------------------------------------------------

    async def _update_settings(self, delta) -> dict[str, Any]:  # type: ignore[override]
        """Reject every programmatic settings change.

        The base implementation mutates ``self._settings`` and notifies
        concrete services of changed fields.  For translate, mid-call
        reconfiguration is unsupported by the model (would require draining
        in-flight audio and re-handshaking), so we raise without mutating.
        """
        raise UnsupportedRealtimeFeatureError(
            "Live Translate model does not support mid-call settings updates."
        )

    async def _run_or_defer_function_calls(
        self, function_calls_llm: list[FunctionCallFromLLM]
    ):
        """Reject any function-call dispatch.

        Reached only if the server unexpectedly emits a tool call frame.
        The Live Translate model does not declare tools, so this is a
        guard against future API surprises.
        """
        raise UnsupportedRealtimeFeatureError(
            "Live Translate model does not support function calling; "
            "received unexpected function call dispatch."
        )

    async def _handle_context(self, context: LLMContext):
        """No-op for translate; raise if context carries a system instruction.

        Translate models ignore conversational context, so we drop incoming
        :class:`LLMContextFrame` silently.  Any system instruction in the
        context is a workflow misconfiguration — raise loudly.

        Sub-gate 5C: revisit if we need to seed any initial state from
        context (currently we do not).
        """
        if any(msg.get("role") == "system" for msg in context.messages):
            raise UnsupportedRealtimeFeatureError(
                "Live Translate model does not support system instructions."
            )
