from pipecat.frames.frames import Frame
from dataclasses import dataclass


@dataclass
class STSUsageFrame(Frame):
    """Custom frame to carry rich multimodal usage from realtime models."""
    usage_metadata: dict


def extract_openai_realtime_sts_usage(evt) -> dict:
    """Extract full audio and text token details from OpenAI response.done event.

    Preserves per-modality cached token breakdown (text, audio, image) from
    ``input_token_details.cached_tokens_details`` when available, consistent
    with the collector's ``_openai_realtime_usage_to_sts_metadata`` helper.
    """
    usage = getattr(evt.response, "usage", None)
    if not usage:
        return {}

    total_in = getattr(usage, "input_tokens", 0) or 0
    total_out = getattr(usage, "output_tokens", 0) or 0
    total = getattr(usage, "total_tokens", 0) or 0

    in_details = getattr(usage, "input_token_details", None)
    out_details = getattr(usage, "output_token_details", None)

    # Inputs
    audio_in = getattr(in_details, "audio_tokens", 0) if in_details else 0
    text_in = getattr(in_details, "text_tokens", 0) if in_details else 0
    image_in = getattr(in_details, "image_tokens", 0) if in_details else 0

    # Cache: prefer per-modality details; fall back to aggregate total
    cached_details = getattr(in_details, "cached_tokens_details", None) if in_details else None
    cached_audio = getattr(cached_details, "audio_tokens", 0) if cached_details else 0
    cached_text = getattr(cached_details, "text_tokens", 0) if cached_details else 0
    cached_image = getattr(cached_details, "image_tokens", 0) if cached_details else 0

    # Also check flat per-modality fields (alternative SDK shapes)
    if not (cached_audio or cached_text or cached_image) and in_details:
        cached_audio = getattr(in_details, "cached_audio_tokens", 0) or 0
        cached_text = getattr(in_details, "cached_text_tokens", 0) or 0
        cached_image = getattr(in_details, "cached_image_tokens", 0) or 0

    cached_total = (
        int(getattr(in_details, "cached_tokens", 0) or 0)
        if in_details
        else int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    )

    # Outputs
    audio_out = getattr(out_details, "audio_tokens", 0) if out_details else 0
    text_out = getattr(out_details, "text_tokens", 0) if out_details else 0

    # Fallback: when no per-modality input split is available, treat non-cached as text
    if not (text_in or audio_in or image_in) and total_in > 0:
        text_in = total_in - cached_total

    meta: dict = {"schemaVersion": 1}
    meta["prompt_tokens"] = total_in
    meta["completion_tokens"] = total_out
    meta["total_tokens"] = total

    if cached_total > 0:
        meta["cache_read_input_tokens"] = cached_total

    input_side: dict = {}
    if text_in > 0:
        input_side["text"] = {"tokens": text_in}
    if audio_in > 0:
        input_side["audio"] = {"tokens": audio_in}
    if image_in > 0:
        input_side["image"] = {"tokens": image_in}
    if input_side:
        meta["input"] = input_side

    output_side: dict = {}
    if text_out > 0:
        output_side["text"] = {"tokens": text_out}
    if audio_out > 0:
        output_side["audio"] = {"tokens": audio_out}
    if output_side:
        meta["output"] = output_side

    # Emit per-modality cached breakdown when available; fall back to aggregate total
    has_split = bool(cached_text or cached_audio or cached_image)
    if has_split:
        cached_side: dict = {}
        if cached_text > 0:
            cached_side["text"] = {"tokens": cached_text}
        if cached_audio > 0:
            cached_side["audio"] = {"tokens": cached_audio}
        if cached_image > 0:
            cached_side["image"] = {"tokens": cached_image}
        if cached_side:
            meta["cached"] = cached_side
    elif cached_total > 0:
        meta["cached"] = {"tokens": cached_total}

    return meta


def _get_list(usage, *keys):
    for k in keys:
        v = usage.get(k) if isinstance(usage, dict) else getattr(usage, k, None)
        if v is not None:
            return list(v) if not isinstance(v, list) else v
    return None


def _optional_int(usage, *keys):
    for k in keys:
        v = usage.get(k) if isinstance(usage, dict) else getattr(usage, k, None)
        if v is not None:
            try:
                return int(v)
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
            mod = getattr(d, "modality", None)
            if mod is None and isinstance(d, dict):
                mod = d.get("modality")
            if mod is None:
                continue
            label = getattr(mod, "name", None) or getattr(mod, "value", None) or mod
            if str(label).upper() != want:
                continue
            tc = getattr(d, "token_count", None)
            if tc is None and isinstance(d, dict):
                tc = d.get("tokenCount") or d.get("token_count")
            total += int(tc or 0)
        except Exception:
            continue
    return total


def extract_google_live_sts_usage(usage) -> dict:
    """Extract full multimodal usage from Google Live UsageMetadata object/dict.

    Correctly accounts for:
    - Tool-use tokens added into ``prompt_tokens`` (``tool_use_prompt_token_count``).
    - Thinking-model tokens (``thoughts_token_count`` / ``thinking_token_count``)
      added into ``completion_tokens``.
    - Accurate ``total_tokens`` reflecting all of the above.
    """
    if usage is None:
        return {}

    prompt_details = _get_list(usage, "prompt_tokens_details", "promptTokensDetails")
    response_details = _get_list(usage, "response_tokens_details", "responseTokensDetails")
    tool_details = _get_list(usage, "tool_use_prompt_tokens_details", "toolUsePromptTokensDetails")
    cache_details = _get_list(usage, "cache_tokens_details", "cacheTokensDetails")

    text_in = (
        _modality_token_count(prompt_details, "TEXT")
        + _modality_token_count(tool_details, "TEXT")
        + _modality_token_count(prompt_details, "DOCUMENT")
        + _modality_token_count(tool_details, "DOCUMENT")
    )
    audio_in = _modality_token_count(prompt_details, "AUDIO") + _modality_token_count(tool_details, "AUDIO")
    image_in = _modality_token_count(prompt_details, "IMAGE") + _modality_token_count(tool_details, "IMAGE")
    video_in = _modality_token_count(prompt_details, "VIDEO") + _modality_token_count(tool_details, "VIDEO")

    # Fallback: when no per-modality prompt details exist, use aggregate counts
    tutc = _optional_int(usage, "tool_use_prompt_token_count", "toolUsePromptTokenCount")
    ptc = _optional_int(usage, "prompt_token_count", "promptTokenCount")

    if not prompt_details and not tool_details:
        # Neither modality list is present: use scalar fallbacks
        if ptc is not None:
            text_in += int(ptc)
        if tutc is not None:
            text_in += int(tutc)
    elif tutc is not None and not tool_details:
        # Prompt details exist but tool_details don't — add tool aggregate
        text_in += int(tutc)

    text_out = (
        _modality_token_count(response_details, "TEXT")
        + _modality_token_count(response_details, "DOCUMENT")
    )
    audio_out = (
        _modality_token_count(response_details, "AUDIO")
        + _modality_token_count(response_details, "VIDEO")
    )

    # Thinking/reasoning tokens (Gemini 2.5+ thinking models)
    thinking_tokens = _optional_int(
        usage,
        "thoughts_token_count", "thoughtsTokenCount",
        "thinking_token_count", "thinkingTokenCount",
    ) or 0

    rtc = _optional_int(usage, "response_token_count", "responseTokenCount")
    if text_out == 0 and audio_out == 0 and rtc is not None:
        # Default fallback to audio output for STS audio connection
        audio_out = int(rtc)

    # Cache breakdowns
    cached_text = _modality_token_count(cache_details, "TEXT") + _modality_token_count(cache_details, "DOCUMENT")
    cached_audio = _modality_token_count(cache_details, "AUDIO") + _modality_token_count(cache_details, "VIDEO")
    cached_image = _modality_token_count(cache_details, "IMAGE")
    cached_legacy = _optional_int(usage, "cached_content_token_count", "cachedContentTokenCount")

    meta: dict = {"schemaVersion": 1}

    # Use the API-reported totals when available; they are the ground truth.
    # tool_use_prompt_token_count is included in prompt_token_count per the API spec,
    # so we do NOT double-add it when ptc is provided.
    reported_ptc = ptc or (text_in + audio_in + image_in + video_in)
    # completion_tokens includes thinking tokens per billing semantics
    reported_rtc = (rtc or (text_out + audio_out)) + thinking_tokens

    meta["prompt_tokens"] = reported_ptc
    meta["completion_tokens"] = reported_rtc
    meta["total_tokens"] = (
        _optional_int(usage, "total_token_count", "totalTokenCount")
        or (reported_ptc + reported_rtc)
    )

    if thinking_tokens > 0:
        meta["thinking_tokens"] = thinking_tokens

    cached_total = cached_legacy or (cached_text + cached_audio + cached_image)
    if cached_total and cached_total > 0:
        meta["cache_read_input_tokens"] = cached_total

    input_side: dict = {}
    if text_in > 0:
        input_side["text"] = {"tokens": text_in}
    if audio_in > 0:
        input_side["audio"] = {"tokens": audio_in}
    if image_in > 0:
        input_side["image"] = {"tokens": image_in}
    if video_in > 0:
        input_side["video"] = {"tokens": video_in}
    if input_side:
        meta["input"] = input_side

    output_side: dict = {}
    if text_out > 0:
        output_side["text"] = {"tokens": text_out}
    if audio_out > 0:
        output_side["audio"] = {"tokens": audio_out}
    if thinking_tokens > 0:
        output_side["thinking"] = {"tokens": thinking_tokens}
    if output_side:
        meta["output"] = output_side

    has_split = bool(cached_text or cached_audio or cached_image)
    if has_split:
        cached_side: dict = {}
        if cached_text > 0:
            cached_side["text"] = {"tokens": cached_text}
        if cached_audio > 0:
            cached_side["audio"] = {"tokens": cached_audio}
        if cached_image > 0:
            cached_side["image"] = {"tokens": cached_image}
        if cached_side:
            meta["cached"] = cached_side
    elif cached_legacy and cached_legacy > 0:
        meta["cached"] = {"tokens": int(cached_legacy)}

    return meta
