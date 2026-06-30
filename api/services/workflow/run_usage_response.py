"""Format workflow run usage for public API responses."""


def format_public_usage_info(usage_info: dict | None) -> dict | None:
    if not usage_info:
        return None

    return {
        "llm": usage_info.get("llm") or {},
        "tts": usage_info.get("tts") or {},
        "stt": usage_info.get("stt") or {},
        "call_duration_seconds": usage_info.get("call_duration_seconds"),
    }


def format_public_cost_info(
    cost_info: dict | None, usage_info: dict | None
) -> dict | None:
    """Return the legacy response shape without doing local cost accounting."""
    duration = None
    if usage_info and usage_info.get("call_duration_seconds") is not None:
        duration = int(round(usage_info.get("call_duration_seconds") or 0))
    elif cost_info and cost_info.get("call_duration_seconds") is not None:
        duration = int(round(cost_info.get("call_duration_seconds") or 0))

    dograh_token_usage = 0
    if cost_info:
        if "dograh_token_usage" in cost_info:
            dograh_token_usage = cost_info.get("dograh_token_usage") or 0
        elif "total_cost_usd" in cost_info:
            dograh_token_usage = round(
                float(cost_info.get("total_cost_usd", 0)) * 100, 2
            )

    if duration is None and dograh_token_usage == 0:
        return None

    return {
        "dograh_token_usage": dograh_token_usage,
        "call_duration_seconds": duration,
    }
