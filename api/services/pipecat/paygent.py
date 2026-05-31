"""
Paygent cost-tracking integration for Dograh pipelines.

Design principles:
  - Pure REST (no SDK dependency). All HTTP calls are fire-and-forget via a
    ThreadPoolExecutor — the pipeline is NEVER blocked.
  - Configuration is read exclusively from environment variables so no secrets
    live in source code.
  - Every code path is wrapped in try/except; a Paygent failure NEVER
    propagates to the calling pipeline.
  - Mode-aware:
      • Standard (STT + LLM + TTS) pipelines:
          - LLM usage tracked per turn via /api/v1/voice/llm
          - TTS usage tracked per turn via /api/v1/voice/tts (with fallback
            estimation for providers like Deepgram WS that don't emit metrics)
          - STT approximated from wall-clock call duration via /api/v1/voice/stt
          - Indicator + billing sent at session end via /api/v1/voice/indicator
      • Realtime / STS (speech-to-speech) pipelines:
          - Each LLM turn forwarded as a STS event via /api/v1/voice/sts with
            the raw usageMetadata payload. No separate STT/TTS events are sent —
            the STS endpoint handles the full multimodal cost.
          - Indicator + billing sent at session end via /api/v1/voice/indicator
  - Docker-aware: localhost/127.0.0.1 in PAYGENT_BASE_URL is automatically
    rewritten to host.docker.internal when running inside a container.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

import requests

from pipecat.frames.frames import CancelFrame, EndFrame, Frame, MetricsFrame, StartFrame
from pipecat.metrics.metrics import LLMUsageMetricsData, TTSUsageMetricsData
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger("api.services.pipecat.paygent")

# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL = "http://localhost:8082"
_REQUEST_TIMEOUT_SECONDS = 10

# ── Provider detection ────────────────────────────────────────────────────────


def _detect_provider(name: str, fallback: str = "unknown") -> str:
    """Map a processor/model name to a canonical Paygent provider slug dynamically.

    Strips common Pipecat class/service name suffixes in a clean, iterative loop
    to find the base provider. Special-cases Gemini to map to Google.
    """
    if not name:
        return fallback

    # 1. Lowercase and clean the input
    clean_name = name.lower().strip()

    # 2. Handle known exceptions (e.g., Gemini maps to Google in Paygent)
    if "gemini" in clean_name:
        return "google"

    # 3. Strip common Pipecat class/service name suffixes
    # Order matches suffix length/specificity to avoid partial matches
    suffixes = [
        "service", "multimodallive", "realtime",
        "vertex", "llm", "tts", "stt", "helper", "transport"
    ]

    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if clean_name.endswith(suffix):
                clean_name = clean_name[:-len(suffix)].rstrip("_").rstrip("-")
                changed = True
                break

    if clean_name:
        return clean_name

    return fallback


def _resolve_base_url(raw: str) -> str:
    """Rewrite localhost/127.0.0.1 -> host.docker.internal when inside Docker."""
    if os.path.exists("/.dockerenv") or os.environ.get("RUNNING_IN_DOCKER", "").lower() == "true":
        raw = raw.replace("127.0.0.1", "host.docker.internal")
        raw = raw.replace("localhost", "host.docker.internal")
    return raw.rstrip("/")


def _google_live_usage_to_sts_metadata(usage: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure Python translation of Google GenAI Live usage_metadata to
    Paygent's canonical speech-to-speech /api/v1/voice/speech-to-speech API schema.
    """
    if not usage:
        return {"schemaVersion": 1}

    def _get_val(obj, *keys):
        if not obj:
            return None
        for k in keys:
            if isinstance(obj, dict):
                if k in obj: return obj[k]
            else:
                if hasattr(obj, k): return getattr(obj, k)
        return None

    def _get_list(obj, *keys):
        val = _get_val(obj, *keys)
        if val is None:
            return None
        return list(val) if not isinstance(val, list) else val

    def _optional_int(obj, *keys):
        val = _get_val(obj, *keys)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                return None
        return None

    def _modality_token_count(details, modality_name):
        if not details:
            return 0
        want = modality_name.upper()
        total = 0
        for d in details:
            try:
                mod = _get_val(d, "modality")
                if mod is None:
                    continue
                label = _get_val(mod, "name") or _get_val(mod, "value") or mod
                if str(label).upper() != want:
                    continue
                tc = _get_val(d, "token_count", "tokenCount")
                total += int(tc or 0)
            except Exception:
                continue
        return total

    prompt_details = _get_list(usage, "prompt_tokens_details", "promptTokensDetails")
    response_details = _get_list(usage, "response_tokens_details", "responseTokensDetails")
    tool_details = _get_list(usage, "tool_use_prompt_tokens_details", "toolUsePromptTokensDetails")
    cache_details = _get_list(usage, "cache_tokens_details", "cacheTokensDetails")

    # input side: TEXT + DOCUMENT + AUDIO + IMAGE + VIDEO
    text_in = _modality_token_count(prompt_details, "TEXT") + _modality_token_count(tool_details, "TEXT")
    audio_in = _modality_token_count(prompt_details, "AUDIO") + _modality_token_count(tool_details, "AUDIO")
    image_in = _modality_token_count(prompt_details, "IMAGE") + _modality_token_count(tool_details, "IMAGE")
    video_in = _modality_token_count(prompt_details, "VIDEO") + _modality_token_count(tool_details, "VIDEO")
    doc_as_text = _modality_token_count(prompt_details, "DOCUMENT") + _modality_token_count(tool_details, "DOCUMENT")
    text_in += doc_as_text

    # fallback aggregate mapping
    tutc = _optional_int(usage, "tool_use_prompt_token_count", "toolUsePromptTokenCount")
    if tutc is not None and not tool_details:
        text_in += int(tutc)

    ptc = _optional_int(usage, "prompt_token_count", "promptTokenCount")
    if ptc is not None and not prompt_details and not tool_details:
        text_in += int(ptc)

    # output side: TEXT + DOCUMENT + AUDIO + VIDEO
    text_out = _modality_token_count(response_details, "TEXT") + _modality_token_count(response_details, "DOCUMENT")
    audio_out = _modality_token_count(response_details, "AUDIO") + _modality_token_count(response_details, "VIDEO")

    rtc = _optional_int(usage, "response_token_count", "responseTokenCount")
    if text_out == 0 and audio_out == 0 and rtc is not None:
        # Default fallback to audio output for STS audio connection
        audio_out = int(rtc)

    # Cache breakdowns
    cached_text = _modality_token_count(cache_details, "TEXT") + _modality_token_count(cache_details, "DOCUMENT")
    cached_audio = _modality_token_count(cache_details, "AUDIO") + _modality_token_count(cache_details, "VIDEO")
    cached_image = _modality_token_count(cache_details, "IMAGE")
    cached_legacy = _optional_int(usage, "cached_content_token_count", "cachedContentTokenCount")

    # Build response payload
    out = {"schemaVersion": 1}

    # Input Side
    inp = {}
    if text_in > 0: inp["text"] = {"tokens": text_in}
    if audio_in > 0: inp["audio"] = {"tokens": audio_in}
    if image_in > 0: inp["image"] = {"tokens": image_in}
    if video_in > 0: inp["video"] = {"tokens": video_in}
    if inp: out["input"] = inp

    # Output Side
    o = {}
    if text_out > 0: o["text"] = {"tokens": text_out}
    if audio_out > 0: o["audio"] = {"tokens": audio_out}
    if o: out["output"] = o

    # Cached breakdown
    has_split = bool(cached_text or cached_audio or cached_image)
    if cached_legacy is not None and cached_legacy > 0 and not has_split:
        out["cached"] = {"tokens": int(cached_legacy)}
    elif has_split:
        cd = {}
        if cached_text > 0: cd["text"] = {"tokens": cached_text}
        if cached_audio > 0: cd["audio"] = {"tokens": cached_audio}
        if cached_image > 0: cd["image"] = {"tokens": cached_image}
        if cd: out["cached"] = cd

    return out


def _openai_realtime_usage_to_sts_metadata(usage: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure Python translation of OpenAI Realtime usage_metadata to
    Paygent's canonical speech-to-speech /api/v1/voice/speech-to-speech API schema.
    """
    if not usage:
        return {"schemaVersion": 1}

    def _get_val(obj, *keys):
        if not obj:
            return None
        for k in keys:
            if isinstance(obj, dict):
                if k in obj: return obj[k]
            else:
                if hasattr(obj, k): return getattr(obj, k)
        return None

    total_in = int(_get_val(usage, "input_tokens", "inputTokens") or 0)
    total_out = int(_get_val(usage, "output_tokens", "outputTokens") or 0)

    in_details = _get_val(usage, "input_token_details", "inputTokenDetails") or {}
    out_details = _get_val(usage, "output_token_details", "outputTokenDetails") or {}

    audio_in = int(_get_val(in_details, "audio_tokens", "audioTokens") or 0)
    text_in = int(_get_val(in_details, "text_tokens", "textTokens") or 0)
    image_in = int(_get_val(in_details, "image_tokens", "imageTokens") or 0)

    cached_total = int(_get_val(usage, "cached_tokens", "cachedTokens") or _get_val(in_details, "cached_tokens", "cachedTokens") or 0)

    cached_details = _get_val(in_details, "cached_tokens_details", "cachedTokensDetails") or {}
    cached_audio = int(_get_val(cached_details, "audio_tokens", "audioTokens") or 0)
    cached_text = int(_get_val(cached_details, "text_tokens", "textTokens") or 0)
    cached_image = int(_get_val(cached_details, "image_tokens", "imageTokens") or 0)

    if not (cached_audio or cached_text or cached_image):
        cached_audio = int(_get_val(in_details, "cached_audio_tokens", "cachedAudioTokens") or 0)
        cached_text = int(_get_val(in_details, "cached_text_tokens", "cachedTextTokens") or 0)
        cached_image = int(_get_val(in_details, "cached_image_tokens", "cachedImageTokens") or 0)

    audio_out = int(_get_val(out_details, "audio_tokens", "audioTokens") or 0)
    text_out = int(_get_val(out_details, "text_tokens", "textTokens") or 0)

    if not (text_in or audio_in or image_in) and total_in > 0:
        text_in = total_in - cached_total

    out = {"schemaVersion": 1}
    inp = {}
    if text_in > 0: inp["text"] = {"tokens": text_in}
    if audio_in > 0: inp["audio"] = {"tokens": audio_in}
    if image_in > 0: inp["image"] = {"tokens": image_in}
    if inp: out["input"] = inp

    o = {}
    if text_out > 0: o["text"] = {"tokens": text_out}
    if audio_out > 0: o["audio"] = {"tokens": audio_out}
    if o: out["output"] = o

    has_split = bool(cached_text or cached_audio or cached_image)
    if cached_total > 0 and not has_split:
        out["cached"] = {"tokens": int(cached_total)}
    elif has_split:
        cd = {}
        if cached_text > 0: cd["text"] = {"tokens": cached_text}
        if cached_audio > 0: cd["audio"] = {"tokens": cached_audio}
        if cached_image > 0: cd["image"] = {"tokens": cached_image}
        if cd: out["cached"] = cd

    return out
# ── Main aggregator ───────────────────────────────────────────────────────────


class PaygentPipelineMetricsAggregator(FrameProcessor):
    """
    Pipecat FrameProcessor that intercepts MetricsFrames from the pipeline
    and forwards usage events to the Paygent REST API.

    Operates in two distinct modes controlled by the ``is_realtime`` flag:

    Standard mode (STT + LLM + TTS):
        - Fires /api/v1/voice/llm per LLM turn.
        - Fires /api/v1/voice/tts per TTS turn (with fallback estimation for
          providers like Deepgram WebSocket that do not emit usage metrics).
        - Fires /api/v1/voice/stt at session end using call wall-clock duration
          as the proxy for audio minutes (Pipecat does not expose raw STT mins).
        - Fires /api/v1/voice/indicator at session end.

    Realtime / STS mode (speech-to-speech — e.g. OpenAI Realtime, Gemini Live):
        - Fires /api/v1/voice/sts per LLM turn with the raw usageMetadata payload.
          The STS endpoint handles the complete multimodal cost (audio-in,
          audio-out, text tokens). No separate STT or TTS events are sent.
        - Fires /api/v1/voice/indicator at session end.

    All HTTP calls are fire-and-forget (ThreadPoolExecutor). Pipeline latency
    is NEVER impacted. All failures are silently logged.
    """

    def __init__(
        self,
        api_key: str,
        agent_id: str,
        customer_id: str,
        session_id: str,
        indicator: str = "per-minute",
        base_url: str = _DEFAULT_BASE_URL,
        is_realtime: bool = False,
        stt_provider: str = "unknown",
        stt_model: str = "default",
        tts_provider: str = "unknown",
        tts_model: str = "default",
        llm_provider: str = "unknown",
        llm_model: str = "default",
    ) -> None:
        super().__init__()
        self._api_key = api_key
        self._agent_id = agent_id
        self._customer_id = customer_id
        self._session_id = session_id
        self._indicator = indicator
        self._base_url = _resolve_base_url(base_url)
        self._is_realtime = is_realtime
        self._stt_provider = stt_provider
        self._stt_model = stt_model
        self._tts_provider = tts_provider
        self._tts_model = tts_model
        self._llm_provider = llm_provider
        self._llm_model = llm_model

        self._start_time: Optional[float] = None
        self._finalized: bool = False

        # Standard mode only — used for TTS fallback estimation
        self._has_received_tts_metrics: bool = False
        self._accumulated_completion_tokens: int = 0

        # De-duplicate MetricsFrame objects that travel multiple pipeline branches
        self._seen_frame_ids: set[int] = set()

        # Fire-and-forget thread pool — daemon threads die with the process
        self._executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="paygent_worker",
        )

        logger.debug(
            "[Paygent] Aggregator ready — session=%s agent=%s customer=%s "
            "base_url=%s is_realtime=%s",
            self._session_id,
            self._agent_id,
            self._customer_id,
            self._base_url,
            self._is_realtime,
        )

    # ── Frame processing ──────────────────────────────────────────────────────

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        try:
            # Guarantee session is registered even if StartFrame was dropped by another processor
            if self._start_time is None and not isinstance(frame, (EndFrame, CancelFrame)):
                self._start_time = time.monotonic()
                self._fire_ensure_customer()
                self._fire_initialize_session()

            if isinstance(frame, StartFrame):
                pass  # Already handled by the lazy init above

            elif isinstance(frame, MetricsFrame):
                frame_id = id(frame)
                if frame_id not in self._seen_frame_ids:
                    self._seen_frame_ids.add(frame_id)
                    for item in frame.data:
                        if isinstance(item, LLMUsageMetricsData):
                            if self._is_realtime:
                                # STS: forward as multimodal STS event
                                self._fire_sts_event(item)
                            else:
                                # Standard: forward as LLM token event
                                self._fire_llm_event(item)
                        elif isinstance(item, TTSUsageMetricsData):
                            # In STS mode the realtime model handles TTS internally —
                            # never send a separate TTS event.
                            if not self._is_realtime:
                                self._fire_tts_event(item)

            elif isinstance(frame, (EndFrame, CancelFrame)):
                self._finalize_session()

        except Exception as exc:
            # Should never reach here, but guarantee the pipeline is not blocked
            logger.error("[Paygent] Unexpected error in process_frame: %s", exc)

        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        """Called by the pipeline task on shutdown."""
        self._finalize_session()
        self._executor.shutdown(wait=False)
        await super().cleanup()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _post(self, path: str, payload: Dict[str, Any]) -> None:
        """Submit a fire-and-forget POST to the Paygent REST API."""
        url = f"{self._base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "paygent-api-key": self._api_key,
        }

        def _do_post() -> None:
            for attempt in range(3):
                try:
                    resp = requests.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=_REQUEST_TIMEOUT_SECONDS,
                    )
                    
                    # Retry on server errors or rate limits
                    if resp.status_code >= 500 or resp.status_code == 429:
                        if attempt < 2:
                            logger.debug("[Paygent] Retry %d posting to %s (HTTP %s)", attempt + 1, path, resp.status_code)
                            time.sleep(1)
                            continue
                            
                    if resp.status_code >= 400:
                        logger.error(
                            "[Paygent] HTTP %s posting to %s: %s",
                            resp.status_code,
                            path,
                            resp.text[:200],
                        )
                    else:
                        logger.info("[Paygent] OK %s -> %s", path, resp.status_code)
                        
                    break  # Success or non-retriable error
                    
                except requests.exceptions.RequestException as exc:
                    if attempt < 2:
                        logger.debug("[Paygent] Retry %d posting to %s due to exception: %s", attempt + 1, path, exc)
                        time.sleep(1)
                    else:
                        logger.error("[Paygent] Exception posting to %s after 3 attempts: %s", path, exc)
                except Exception as exc:
                    logger.error("[Paygent] Unhandled exception posting to %s: %s", path, exc)
                    break

        try:
            self._executor.submit(_do_post)
        except RuntimeError:
            # Executor already shut down — happens during late cleanup calls
            logger.debug("[Paygent] Executor shut down; skipping post to %s", path)

    def _fire_ensure_customer(self) -> None:
        """Idempotently register the customer in the tracking service."""
        try:
            self._post(
                "/api/v1/customers/create-or-get",
                {
                    "name": f"Customer {self._customer_id}",
                    "externalId": self._customer_id,
                },
            )
            logger.info(
                "[Paygent] Customer auto-registration queued — customer_id=%s", self._customer_id
            )
        except Exception as exc:
            logger.error("[Paygent] Error queuing customer registration: %s", exc)

    def _fire_initialize_session(self) -> None:
        """Register the voice session on the Paygent service."""
        try:
            self._post(
                "/api/v1/voice/session",
                {
                    "sessionId": self._session_id,
                    "agentId": self._agent_id,
                    "customerId": self._customer_id,
                },
            )
            logger.info(
                "[Paygent] Voice session init queued — session=%s", self._session_id
            )
        except Exception as exc:
            logger.error("[Paygent] Error queuing session init: %s", exc)

    def _fire_llm_event(self, data: LLMUsageMetricsData) -> None:
        """Forward LLM token usage to Paygent (standard STT+LLM+TTS mode only)."""
        try:
            provider = _detect_provider(data.processor or "", fallback=self._llm_provider)
            if provider == "unknown":
                provider = self._llm_provider
            model = data.model or self._llm_model
            usage = data.value

            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            cached_tokens = (
                getattr(usage, "cache_read_input_tokens", 0)
                or getattr(usage, "cached_tokens", 0)
                or 0
            )

            if prompt_tokens <= 0 and completion_tokens <= 0:
                return

            # Track completion tokens for TTS fallback estimation
            self._accumulated_completion_tokens += completion_tokens

            payload: Dict[str, Any] = {
                "sessionId": self._session_id,
                "provider": provider,
                "model": model,
                "plan": "default",
                "promptTokens": prompt_tokens,
                "completionTokens": completion_tokens,
            }
            if cached_tokens > 0:
                payload["cachedTokens"] = cached_tokens

            logger.debug(
                "[Paygent] LLM event — model=%s provider=%s prompt=%d completion=%d cached=%d",
                model, provider, prompt_tokens, completion_tokens, cached_tokens,
            )
            self._post("/api/v1/voice/llm", payload)
        except Exception as exc:
            logger.error("[Paygent] Error building LLM payload: %s", exc)

    def _fire_sts_event(self, data: LLMUsageMetricsData) -> None:
        """Forward realtime/STS usage to Paygent via /api/v1/voice/sts.

        In STS mode the realtime provider (e.g. OpenAI Realtime, Gemini Live)
        handles STT + LLM + TTS internally. The usageMetadata payload forwarded
        here contains all input/output modality details so the tracking service
        can calculate the exact multimodal cost. No separate LLM/STT/TTS events
        are sent in this mode.
        """
        try:
            provider = _detect_provider(data.processor or "", fallback=self._llm_provider)
            if provider == "unknown":
                provider = self._llm_provider

            # Grok and Ultravox are billed purely per-minute at the end of the session.
            # Skip per-turn STS events for these providers; they are handled in _finalize_session.
            if provider in ("grok", "ultravox", "grok_realtime", "ultravox_realtime"):
                return

            model = data.model or self._llm_model
            usage = data.value

            # If the LLMTokenUsage object contains raw_usage_metadata, translate it
            # using our robust multimodal payload mappers.
            raw_metadata = getattr(usage, "raw_usage_metadata", None)
            if raw_metadata:
                if provider in ("openai", "openai_realtime"):
                    usage_metadata = _openai_realtime_usage_to_sts_metadata(raw_metadata)
                else:
                    usage_metadata = _google_live_usage_to_sts_metadata(raw_metadata)
            else:
                # Build usageMetadata dict from the pipecat metrics object.
                # Pipecat currently strips modality information and emits only basic
                # token fields. We map these to text tokens for STS tracking.
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                cached_tokens = (
                    getattr(usage, "cache_read_input_tokens", 0)
                    or getattr(usage, "cached_tokens", 0)
                    or 0
                )

                usage_metadata = {"schemaVersion": 1}
                if prompt_tokens > 0:
                    usage_metadata.setdefault("input", {})["text"] = {"tokens": prompt_tokens}
                if completion_tokens > 0:
                    usage_metadata.setdefault("output", {})["text"] = {"tokens": completion_tokens}
                if cached_tokens > 0:
                    usage_metadata["cached"] = {"tokens": cached_tokens}

                # Also include any additional attributes from the usage object
                if hasattr(usage, "__dict__"):
                    for k, v in vars(usage).items():
                        if not k.startswith("_") and v is not None and k not in usage_metadata:
                            usage_metadata[k] = v

            if not usage_metadata:
                logger.debug(
                    "[Paygent] Skipping empty STS event — session=%s", self._session_id
                )
                return

            logger.debug(
                "[Paygent] STS event — model=%s provider=%s", model, provider
            )
            self._post(
                "/api/v1/voice/speech-to-speech",
                {
                    "sessionId": self._session_id,
                    "provider": provider,
                    "model": model,
                    "plan": "default",
                    "usageMetadata": usage_metadata,
                },
            )
        except Exception as exc:
            logger.error("[Paygent] Error building STS payload: %s", exc)

    def _fire_tts_event(self, data: TTSUsageMetricsData) -> None:
        """Forward TTS character usage to Paygent (standard mode only)."""
        try:
            provider = _detect_provider(data.processor or "", fallback=self._tts_provider)
            if provider == "unknown":
                provider = self._tts_provider
            model = data.model or self._tts_model
            char_count = int(data.value or 0)

            if char_count <= 0:
                return

            self._has_received_tts_metrics = True

            logger.debug(
                "[Paygent] TTS event — model=%s provider=%s chars=%d",
                model, provider, char_count,
            )
            self._post(
                "/api/v1/voice/tts",
                {
                    "sessionId": self._session_id,
                    "provider": provider,
                    "model": model,
                    "plan": "default",
                    "characters": char_count,
                },
            )
        except Exception as exc:
            logger.error("[Paygent] Error building TTS payload: %s", exc)

    def _finalize_session(self) -> None:
        """
        Fire end-of-session tracking events and the billing indicator.

        Standard mode:
          1. /api/v1/voice/stt  — call duration used as audio-minute proxy
          2. /api/v1/voice/tts  — fallback estimation from completion tokens
             (only when no per-turn TTS metrics were received, e.g. Deepgram WS)
          3. /api/v1/voice/indicator — billing indicator with total duration

        STS / realtime mode:
          1. /api/v1/voice/indicator — billing indicator with total duration
             (STS per-turn events already contain the full multimodal cost;
              no STT or TTS events should be sent)
        """
        if self._finalized:
            return
        self._finalized = True

        if self._start_time is None:
            logger.warning(
                "[Paygent] Session never started; skipping finalization — session=%s",
                self._session_id,
            )
            return

        try:
            duration_minutes = (time.monotonic() - self._start_time) / 60.0
            logger.info(
                "[Paygent] Finalizing session=%s mode=%s indicator=%s duration=%.3f min",
                self._session_id,
                "sts" if self._is_realtime else "standard",
                self._indicator,
                duration_minutes,
            )

            if not self._is_realtime:
                # 1. STT — approximate from call wall-clock duration
                self._post(
                    "/api/v1/voice/stt",
                    {
                        "sessionId": self._session_id,
                        "provider": self._stt_provider,
                        "model": self._stt_model,
                        "plan": "default",
                        "audioMinutes": duration_minutes,
                    },
                )

                # 2. TTS fallback — only when the TTS provider never emitted
                #    per-turn metrics (e.g. Deepgram WebSocket TTS). Estimate
                #    using ~4 characters per LLM output token as the proxy.
                if (
                    not self._has_received_tts_metrics
                    and self._accumulated_completion_tokens > 0
                ):
                    estimated_chars = self._accumulated_completion_tokens * 4
                    logger.info(
                        "[Paygent] Fallback TTS — %d completion tokens -> ~%d estimated chars "
                        "provider=%s model=%s",
                        self._accumulated_completion_tokens,
                        estimated_chars,
                        self._tts_provider,
                        self._tts_model,
                    )
                    self._post(
                        "/api/v1/voice/tts",
                        {
                            "sessionId": self._session_id,
                            "provider": self._tts_provider,
                            "model": self._tts_model,
                            "plan": "default",
                            "characters": estimated_chars,
                        },
                    )
            else:
                # STS / realtime mode
                # Grok and Ultravox are billed purely on wall-clock minutes.
                # Track the final session duration as 'connection.minutes'.
                if self._llm_provider in ("grok", "ultravox", "grok_realtime", "ultravox_realtime"):
                    logger.info(
                        "[Paygent] Sending per-minute STS usage for provider=%s model=%s minutes=%.3f",
                        self._llm_provider,
                        self._llm_model,
                        duration_minutes,
                    )
                    self._post(
                        "/api/v1/voice/speech-to-speech",
                        {
                            "sessionId": self._session_id,
                            "provider": self._llm_provider,
                            "model": self._llm_model,
                            "plan": "default",
                            "usageMetadata": {
                                "schemaVersion": 1,
                                "connection": {"minutes": duration_minutes},
                            },
                        },
                    )

            # Always send the billing indicator regardless of mode
            self._post(
                "/api/v1/voice/indicator",
                {
                    "sessionId": self._session_id,
                    "indicator": self._indicator,
                    "totalDuration": duration_minutes,
                },
            )

        except Exception as exc:
            logger.error("[Paygent] Error finalizing session: %s", exc)
