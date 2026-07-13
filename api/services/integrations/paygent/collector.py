"""Paygent live-call collector.

Attaches to the pipecat pipeline as a ``BaseObserver`` to accumulate per-call
usage metrics (STT audio seconds, LLM tokens, TTS characters, STS metadata)
in memory during the call.  No network I/O happens here; all delivery is
deferred to the post-call completion handler.

Design mirrors ``api/services/integrations/tuner/collector.py`` exactly:
- Attach to the task in ``PaygentRuntimeSession.attach``
- Build a serialisable snapshot in ``build_snapshot``
- Return it from ``on_call_finished`` so it lands in ``workflow_run.logs``
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    MetricsFrame,
    StartFrame,
    TextFrame,
)
from pipecat.metrics.metrics import (
    LLMTokenUsage,
    LLMUsageMetricsData,
    TTSUsageMetricsData,
)
from api.services.pipecat.realtime.paygent_sts_frames import STSUsageFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection


def _detect_provider(name: str, fallback: str = "unknown") -> str:
    """Map a processor/model name to a canonical Paygent provider slug dynamically."""
    if not name:
        return fallback
    clean_name = name.lower().strip()
    if "gemini" in clean_name:
        return "google"
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
    return clean_name or fallback


@dataclass
class _UsageAccumulator:
    """In-memory accumulator for per-call usage data."""

    # STT
    stt_audio_seconds: float = 0.0

    # LLM (aggregated across all turns)
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_cached_tokens: int = 0

    # TTS
    tts_characters: int = 0
    _has_tts_metrics: bool = False

    # STS / realtime (last seen usage_metadata dict; callers merge these)
    sts_usage_metadata: dict[str, Any] | None = None

    # Call timing
    call_start_abs_ns: int = field(default_factory=time.time_ns)
    call_end_abs_ns: int | None = None
    
    @property
    def total_duration_seconds(self) -> int:
        if self.call_end_abs_ns is None:
            return int((time.time_ns() - self.call_start_abs_ns) / 1_000_000_000)
        return int((self.call_end_abs_ns - self.call_start_abs_ns) / 1_000_000_000)
        
    def get_stt_audio_seconds(self) -> float:
        """Return measured STT audio seconds accumulated from the pipeline.

        NOTE: This is the real measured STT audio duration collected from the
        pipeline's STT metrics frames, NOT the total call wall-clock duration.
        The call wall-clock duration is available separately via
        ``total_duration_seconds``.
        """
        return self.stt_audio_seconds

    def add_llm(self, usage: LLMTokenUsage) -> None:
        self.llm_prompt_tokens += usage.prompt_tokens or 0
        self.llm_completion_tokens += usage.completion_tokens or 0
        self.llm_cached_tokens += (usage.cache_read_input_tokens or 0) + (
            usage.cache_creation_input_tokens or 0
        )

    def add_tts_metrics(self, data: Any) -> None:
        if not self._has_tts_metrics:
            self._has_tts_metrics = True
            self.tts_characters = 0  # Ignore manual count if metrics emit natively
        
        # Extremely robust extraction
        val = 0
        if isinstance(data, (int, float)):
            val = data
        elif hasattr(data, "value"):
            val = getattr(data, "value", 0) or 0
        elif hasattr(data, "characters"):
            val = getattr(data, "characters", 0) or 0
        elif isinstance(data, dict):
            val = data.get("value") or data.get("characters") or 0
            
        try:
            self.tts_characters += int(val or 0)
        except Exception:
            pass

    def add_tts_manual(self, text: str) -> None:
        if not self._has_tts_metrics:
            self.tts_characters += len(text)

    def finalize(self) -> None:
        if self.call_end_abs_ns is None:
            self.call_end_abs_ns = time.time_ns()


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


def _merge_sts_metadata(existing: dict, new: dict) -> dict:
    if not existing:
        return new
    out = {"schemaVersion": 1}
    for key in ("input", "output", "cached"):
        e_val = existing.get(key, {})
        n_val = new.get(key, {})
        if not e_val and not n_val:
            continue
        
        merged_cat: dict = {}

        # Prefer per-modality merge when either side has per-modality detail.
        # Only use the flat aggregate{"tokens": N} form when neither side has
        # any per-modality breakdown at all (e.g. legacy schema).
        e_has_modalities = any(m in e_val for m in ("text", "audio", "image", "video"))
        n_has_modalities = any(m in n_val for m in ("text", "audio", "image", "video"))

        if e_has_modalities or n_has_modalities:
            for modality in ("text", "audio", "image", "video"):
                e_mod = e_val.get(modality, {}).get("tokens", 0)
                n_mod = n_val.get(modality, {}).get("tokens", 0)
                total = e_mod + n_mod
                if total > 0:
                    merged_cat[modality] = {"tokens": total}
            # Also sum any lingering aggregate total so no tokens are lost
            e_agg = e_val.get("tokens", 0) if not e_has_modalities else 0
            n_agg = n_val.get("tokens", 0) if not n_has_modalities else 0
            if e_agg or n_agg:
                # Incorporate the unbroken-down side into the "text" bucket as
                # a best-effort attribution rather than silently dropping it.
                existing_text = merged_cat.get("text", {}).get("tokens", 0)
                merged_cat["text"] = {"tokens": existing_text + e_agg + n_agg}
        elif "tokens" in e_val or "tokens" in n_val:
            merged_cat["tokens"] = e_val.get("tokens", 0) + n_val.get("tokens", 0)

        if merged_cat:
            out[key] = merged_cat
            
    # retain any other keys, summing up numeric ones to keep metadata consistent
    for k, v in existing.items():
        if k not in ("schemaVersion", "input", "output", "cached"):
            out[k] = v
    for k, v in new.items():
        if k not in ("schemaVersion", "input", "output", "cached"):
            if k in out and isinstance(out[k], (int, float)) and isinstance(v, (int, float)):
                out[k] = out[k] + v
            else:
                out[k] = v
            
    return out

class PaygentCollector(BaseObserver):
    """Pipecat observer that accumulates usage data for a single call.

    Accumulates:
    - LLM token usage from ``MetricsFrame / LLMUsageMetricsData``
    - TTS character usage from ``MetricsFrame / TTSUsageMetricsData``
    - STT audio seconds from ``MetricsFrame`` (when exposed by the pipeline)
    - Call start / end timestamps for ``total_duration_seconds``

    Does **not** do any network I/O.
    """

    def __init__(
        self,
        *,
        workflow_run_id: int,
        is_realtime: bool,
        stt_provider: str = "",
        stt_model: str = "",
        llm_provider: str = "",
        llm_model: str = "",
        tts_provider: str = "",
        tts_model: str = "",
        sts_provider: str = "",
        sts_model: str = "",
    ) -> None:
        super().__init__()
        self._workflow_run_id = workflow_run_id
        self._is_realtime = is_realtime
        self._stt_provider = stt_provider
        self._stt_model = stt_model
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._tts_provider = tts_provider
        self._tts_model = tts_model
        self._sts_provider = sts_provider
        self._sts_model = sts_model
        self._acc = _UsageAccumulator()
        self._call_disposition: str = "completed"
        # Dedup guard – pipecat sometimes re-delivers frames.
        # Use a bounded deque+set pattern (mirrors tuner/collector.py) so that
        # clearing never creates a window where previously-seen frames are
        # double-counted after a reset.
        self._seen_frame_ids: set[int] = set()
        self._frame_history: deque[int] = deque(maxlen=2000)

    # ------------------------------------------------------------------
    # Public hooks
    # ------------------------------------------------------------------

    def set_call_disposition(self, disposition: str | None) -> None:
        if disposition:
            self._call_disposition = disposition

    def build_snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict stored in ``workflow_run.logs``."""
        self._acc.finalize()
        stt_audio_sec = self._acc.get_stt_audio_seconds()

        return {
            "workflow_run_id": self._workflow_run_id,
            "is_realtime": self._is_realtime,
            "stt_provider": self._stt_provider,
            "stt_model": self._stt_model,
            "stt_audio_seconds": stt_audio_sec,
            "llm_provider": self._llm_provider,
            "llm_model": self._llm_model,
            "llm_prompt_tokens": self._acc.llm_prompt_tokens,
            "llm_completion_tokens": self._acc.llm_completion_tokens,
            "llm_cached_tokens": self._acc.llm_cached_tokens,
            "tts_provider": self._tts_provider,
            "tts_model": self._tts_model,
            "tts_characters": self._acc.tts_characters,
            "sts_provider": self._sts_provider,
            "sts_model": self._sts_model,
            "sts_usage_metadata": self._acc.sts_usage_metadata,
            "call_disposition": self._call_disposition,
            "total_duration_seconds": self._acc.total_duration_seconds,
        }

    # ------------------------------------------------------------------
    # BaseObserver implementation
    # ------------------------------------------------------------------

    async def on_push_frame(self, data: FramePushed) -> None:  # type: ignore[override]
        try:
            # Only process downstream frames; ignore upstream (mic → STT direction)
            if data.direction != FrameDirection.DOWNSTREAM:
                return

            frame = data.frame

            # Dedup: bounded LRU set – rebuilds from deque when overfull so we
            # never create a gap where previously seen frames are re-processed.
            if frame.id in self._seen_frame_ids:
                return
            self._seen_frame_ids.add(frame.id)
            self._frame_history.append(frame.id)
            if len(self._seen_frame_ids) > len(self._frame_history):
                self._seen_frame_ids = set(self._frame_history)

            if isinstance(frame, StartFrame):
                self._acc.call_start_abs_ns = time.time_ns()

            elif isinstance(frame, MetricsFrame):
                for item in frame.data:
                    if isinstance(item, LLMUsageMetricsData):
                        is_sts_frame = False
                        proc_lower = (item.processor or "").lower()
                        if getattr(self, "_is_realtime", False):
                            if "realtime" in proc_lower or "live" in proc_lower:
                                is_sts_frame = True

                        if is_sts_frame:
                            # Normalise the raw provider slug so that variants like
                            # "openai_realtime", "azure_realtime", etc. route correctly.
                            raw_provider = (
                                getattr(self, "_sts_provider", "") or getattr(self, "_llm_provider", "")
                            )
                            provider = _detect_provider(raw_provider) if raw_provider else "unknown"
                            if provider not in ("grok", "ultravox"):
                                usage = item.value
                                raw_metadata = getattr(usage, "raw_usage_metadata", None)
                                if raw_metadata:
                                    # OpenAI Realtime and Azure Realtime (azure→openai via _detect_provider)
                                    # share the same wire format.
                                    if provider in ("openai", "azure"):
                                        new_meta = _openai_realtime_usage_to_sts_metadata(raw_metadata)
                                    else:
                                        new_meta = _google_live_usage_to_sts_metadata(raw_metadata)
                                else:
                                    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                                    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                                    cached_tokens = (getattr(usage, "cache_read_input_tokens", 0) or getattr(usage, "cached_tokens", 0) or 0)
                                    new_meta = {"schemaVersion": 1}
                                    if prompt_tokens > 0:
                                        new_meta.setdefault("input", {})["text"] = {"tokens": prompt_tokens}
                                    if completion_tokens > 0:
                                        new_meta.setdefault("output", {})["text"] = {"tokens": completion_tokens}
                                    if cached_tokens > 0:
                                        new_meta["cached"] = {"tokens": cached_tokens}
                                    
                                    if hasattr(usage, "__dict__"):
                                        for k, v in vars(usage).items():
                                            if not k.startswith("_") and v is not None and k not in new_meta:
                                                new_meta[k] = v
                                
                                self._acc.sts_usage_metadata = _merge_sts_metadata(
                                    self._acc.sts_usage_metadata or {}, new_meta
                                )
                        else:
                            self._acc.add_llm(item.value)
                    elif isinstance(item, TTSUsageMetricsData):
                        chars_val = getattr(item, "value", 0) or 0
                        self._acc.add_tts_metrics(chars_val)
                    # STT usage is exposed as a float in TTSUsageMetricsData-like
                    # structure by some providers; we also pull from the aggregator
                    # snapshot at call-finish (see runtime.py) for robustness.

            elif isinstance(frame, STSUsageFrame):
                self._acc.sts_usage_metadata = _merge_sts_metadata(
                    self._acc.sts_usage_metadata or {}, frame.usage_metadata
                )

            elif isinstance(frame, TextFrame):
                # Fallback character counting for providers that don't emit metrics (like Cartesia)
                self._acc.add_tts_manual(frame.text)

            elif isinstance(frame, (EndFrame, CancelFrame)):
                self._acc.finalize()
        except Exception as exc:
            pass
