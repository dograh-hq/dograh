from pipecat.frames.frames import Frame
from dataclasses import dataclass

@dataclass
class STSUsageFrame(Frame):
    """Custom frame to carry rich multimodal usage from realtime models."""
    usage_metadata: dict

def extract_openai_realtime_sts_usage(evt) -> dict:
    """Extract full audio and text token details from OpenAI response.done event."""
    usage = getattr(evt.response, "usage", None)
    if not usage:
        return {}

    total_in = getattr(usage, "input_tokens", 0)
    total_out = getattr(usage, "output_tokens", 0)
    total = getattr(usage, "total_tokens", 0)

    in_details = getattr(usage, "input_token_details", None)
    out_details = getattr(usage, "output_token_details", None)

    # Inputs
    audio_in = getattr(in_details, "audio_tokens", 0) if in_details else 0
    text_in = getattr(in_details, "text_tokens", 0) if in_details else 0
    cached_total = getattr(in_details, "cached_tokens", 0) if in_details else getattr(usage, "cache_read_input_tokens", 0)

    # Outputs
    audio_out = getattr(out_details, "audio_tokens", 0) if out_details else 0
    text_out = getattr(out_details, "text_tokens", 0) if out_details else 0

    # Fallback
    if not (text_in or audio_in) and total_in > 0:
        text_in = total_in - cached_total

    meta = {"schemaVersion": 1}
    meta["prompt_tokens"] = total_in
    meta["completion_tokens"] = total_out
    meta["total_tokens"] = total
    
    if cached_total > 0:
        meta["cache_read_input_tokens"] = cached_total

    input_side = {}
    if text_in > 0:
        input_side["text"] = {"tokens": text_in}
    if audio_in > 0:
        input_side["audio"] = {"tokens": audio_in}
    
    output_side = {}
    if text_out > 0:
        output_side["text"] = {"tokens": text_out}
    if audio_out > 0:
        output_side["audio"] = {"tokens": audio_out}
    
    if input_side:
        meta["input"] = input_side
    if output_side:
        meta["output"] = output_side
    
    if cached_total > 0:
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
    """Extract full multimodal usage from Google Live UsageMetadata object/dict."""
    if usage is None:
        return {}

    prompt_details = _get_list(usage, "prompt_tokens_details", "promptTokensDetails")
    response_details = _get_list(usage, "response_tokens_details", "responseTokensDetails")
    tool_details = _get_list(usage, "tool_use_prompt_tokens_details", "toolUsePromptTokensDetails")
    cache_details = _get_list(usage, "cache_tokens_details", "cacheTokensDetails")

    text_in = _modality_token_count(prompt_details, "TEXT") + _modality_token_count(tool_details, "TEXT") + _modality_token_count(prompt_details, "DOCUMENT") + _modality_token_count(tool_details, "DOCUMENT")
    audio_in = _modality_token_count(prompt_details, "AUDIO") + _modality_token_count(tool_details, "AUDIO")
    image_in = _modality_token_count(prompt_details, "IMAGE") + _modality_token_count(tool_details, "IMAGE")
    video_in = _modality_token_count(prompt_details, "VIDEO") + _modality_token_count(tool_details, "VIDEO")

    tutc = _optional_int(usage, "tool_use_prompt_token_count", "toolUsePromptTokenCount")
    if tutc is not None and not tool_details:
        text_in += int(tutc)

    ptc = _optional_int(usage, "prompt_token_count", "promptTokenCount")
    if ptc is not None and not prompt_details and not tool_details:
        text_in += int(ptc)

    text_out = _modality_token_count(response_details, "TEXT") + _modality_token_count(response_details, "DOCUMENT")
    audio_out = _modality_token_count(response_details, "AUDIO") + _modality_token_count(response_details, "VIDEO")

    rtc = _optional_int(usage, "response_token_count", "responseTokenCount")
    if text_out == 0 and audio_out == 0 and rtc is not None:
        audio_out = int(rtc)

    cached_text = _modality_token_count(cache_details, "TEXT") + _modality_token_count(cache_details, "DOCUMENT")
    cached_audio = _modality_token_count(cache_details, "AUDIO") + _modality_token_count(cache_details, "VIDEO")
    cached_image = _modality_token_count(cache_details, "IMAGE")
    cached_legacy = _optional_int(usage, "cached_content_token_count", "cachedContentTokenCount")

    meta = {"schemaVersion": 1}
    
    total_prompt = ptc or text_in + audio_in + image_in + video_in
    total_completion = rtc or text_out + audio_out
    
    meta["prompt_tokens"] = total_prompt
    meta["completion_tokens"] = total_completion
    meta["total_tokens"] = _optional_int(usage, "total_token_count", "totalTokenCount") or (total_prompt + total_completion)

    cached_total = cached_legacy or (cached_text + cached_audio + cached_image)
    if cached_total and cached_total > 0:
        meta["cache_read_input_tokens"] = cached_total

    input_side = {}
    if text_in > 0: input_side["text"] = {"tokens": text_in}
    if audio_in > 0: input_side["audio"] = {"tokens": audio_in}
    if image_in > 0: input_side["image"] = {"tokens": image_in}
    if video_in > 0: input_side["video"] = {"tokens": video_in}
    
    output_side = {}
    if text_out > 0: output_side["text"] = {"tokens": text_out}
    if audio_out > 0: output_side["audio"] = {"tokens": audio_out}
    
    if input_side: meta["input"] = input_side
    if output_side: meta["output"] = output_side
    
    if cached_text or cached_audio or cached_image:
        meta["cached"] = {}
        if cached_text > 0: meta["cached"]["text"] = {"tokens": cached_text}
        if cached_audio > 0: meta["cached"]["audio"] = {"tokens": cached_audio}
        if cached_image > 0: meta["cached"]["image"] = {"tokens": cached_image}
    elif cached_legacy and cached_legacy > 0:
        meta["cached"] = {"tokens": int(cached_legacy)}

    return meta
