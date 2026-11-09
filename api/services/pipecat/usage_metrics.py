"""Provider usage metrics and shared normalization of persisted token totals."""

from pipecat.metrics.metrics import MetricsData


class LiveUsageMetricsData(MetricsData):
    """Additional seconds reported by a duration-billed Live session."""

    seconds: float


def llm_usage_entries(usage) -> list[dict]:
    """Accept current processor maps and legacy flat token records."""
    if not isinstance(usage, dict):
        return []
    if any(
        key in usage for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    ):
        return [usage]
    return [entry for entry in usage.values() if isinstance(entry, dict)]


def gross_prompt_tokens(usage: dict) -> int:
    """Use gross totals across providers; legacy records fall back to prompt_tokens.

    Anthropic reports prompt_tokens net of cached tokens, unlike Google/OpenAI.
    A total_tokens value includes both and avoids double counting cache reads.
    """
    # Some legacy records use zero for an unavailable total. A missing or
    # inconsistent total must never erase the input tokens actually reported.
    return max(
        0,
        usage.get("prompt_tokens") or 0,
        (usage.get("total_tokens") or 0) - (usage.get("completion_tokens") or 0),
    )
