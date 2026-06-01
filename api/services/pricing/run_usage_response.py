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
