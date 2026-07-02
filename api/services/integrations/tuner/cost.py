"""Per-call cost computation for the Tuner export.

Dograh no longer rates calls locally, so when a user wants Tuner to show a
cost they provide their own per-unit prices on the Tuner node (the "bring your
own keys" model). This module turns those rates plus the call's measured usage
(`workflow_run.usage_info`) into a single `call_cost` value in cents, which is
what Tuner's public API stores.

Rates are optional: a blank rate contributes nothing. Usage metrics come from
the pipeline aggregator and are reliable for LLM tokens and TTS characters.
STT seconds are not measured, so the STT and telephony rates are applied
per-minute against the call's wall-clock duration (an approximation).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .node import TunerNodeData


def _sum_llm_tokens(usage_info: dict[str, Any]) -> tuple[int, int, int]:
    """Sum prompt, completion, and cached-input tokens across all llm entries.

    Cached-input tokens (``cache_read_input_tokens``) are reported as a discounted
    subset of ``prompt_tokens`` (OpenAI convention), not in addition to it.
    """
    prompt_tokens = 0
    completion_tokens = 0
    cached_tokens = 0
    for entry in (usage_info.get("llm") or {}).values():
        if isinstance(entry, dict):
            prompt_tokens += entry.get("prompt_tokens") or 0
            completion_tokens += entry.get("completion_tokens") or 0
            cached_tokens += entry.get("cache_read_input_tokens") or 0
    return prompt_tokens, completion_tokens, cached_tokens


def _sum_tts_characters(usage_info: dict[str, Any]) -> int:
    """Sum TTS characters across every tts processor/model entry."""
    total = 0
    for value in (usage_info.get("tts") or {}).values():
        if isinstance(value, (int, float)):
            total += value
    return int(total)


# Transcript roles that represent bot-spoken text sent to TTS. Excludes
# "user" (STT input) and "agent_function"/"agent_result" (tool calls).
_SPOKEN_ROLES = {"agent", "assistant", "bot"}


def _count_transcript_tts_characters(
    transcript_segments: list[dict[str, Any]] | None,
) -> int:
    """Count characters of bot-spoken transcript turns (TTS proxy).

    Used when the pipeline did not measure TTS characters directly (e.g. the
    Deepgram websocket TTS service does not emit usage metrics). The spoken
    transcript text closely matches what was sent to the TTS engine.
    """
    if not transcript_segments:
        return 0
    total = 0
    for segment in transcript_segments:
        if isinstance(segment, dict) and segment.get("role") in _SPOKEN_ROLES:
            total += len(segment.get("text") or "")
    return total


def compute_call_cost_cents(
    tuner_data: "TunerNodeData",
    usage_info: dict[str, Any] | None,
    transcript_segments: list[dict[str, Any]] | None = None,
) -> float | None:
    """Compute the call cost in cents from node rates and measured usage.

    Returns ``None`` when cost calculation is disabled or no rates are
    configured, so the caller can omit ``call_cost`` from the payload entirely
    rather than report a misleading zero.
    """
    if not tuner_data.cost_calculation_enabled:
        return None

    raw_rates = (
        tuner_data.cost_llm_input_rate,
        tuner_data.cost_llm_cached_input_rate,
        tuner_data.cost_llm_output_rate,
        tuner_data.cost_tts_rate,
        tuner_data.cost_stt_rate,
        tuner_data.cost_telephony_rate,
    )
    if all(rate is None for rate in raw_rates):
        return None

    usage_info = usage_info or {}
    prompt_tokens, completion_tokens, cached_tokens = _sum_llm_tokens(usage_info)
    # Prefer the pipeline-measured TTS characters; fall back to the spoken
    # transcript when the TTS service did not report usage (e.g. Deepgram websocket).
    tts_characters = _sum_tts_characters(usage_info)
    if tts_characters == 0:
        tts_characters = _count_transcript_tts_characters(transcript_segments)
    duration_minutes = (usage_info.get("call_duration_seconds") or 0) / 60.0

    llm_input_rate = tuner_data.cost_llm_input_rate or 0.0
    cached_input_rate = tuner_data.cost_llm_cached_input_rate
    llm_output_rate = tuner_data.cost_llm_output_rate or 0.0
    tts_rate = tuner_data.cost_tts_rate or 0.0
    stt_rate = tuner_data.cost_stt_rate or 0.0
    telephony_rate = tuner_data.cost_telephony_rate or 0.0

    # Cached tokens are a discounted subset of prompt tokens. Only split them out
    # when a cached rate is configured; otherwise bill all prompt tokens normally.
    if cached_input_rate is not None:
        uncached_prompt_tokens = max(prompt_tokens - cached_tokens, 0)
        llm_input_usd = (
            uncached_prompt_tokens * llm_input_rate + cached_tokens * cached_input_rate
        ) / 1_000_000
    else:
        llm_input_usd = prompt_tokens * llm_input_rate / 1_000_000

    cost_usd = (
        llm_input_usd
        + completion_tokens * llm_output_rate / 1_000_000
        + tts_characters * tts_rate / 1_000
        + duration_minutes * stt_rate
        + duration_minutes * telephony_rate
    )

    return round(cost_usd * 100, 4)
