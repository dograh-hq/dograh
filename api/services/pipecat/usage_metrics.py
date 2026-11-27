"""Provider usage metrics and shared normalization of persisted token totals."""

from pipecat.metrics.metrics import LLMTokenUsage, MetricsData


def merge_llm_usage(existing: dict | None, delta: dict) -> dict:
    """Add provider token counts without losing optional or unreported fields."""
    merged = dict(existing or {})
    for key, value in delta.items():
        current = dict(merged.get(key) or {})
        for field, count in value.items():
            if count is not None:
                current[field] = (current.get(field) or 0) + count
            else:
                current.setdefault(field, None)
        merged[key] = current
    return merged


class InferenceUsage:
    """Collect out-of-pipeline usage, including completed retry/fallback attempts."""

    def __init__(self) -> None:
        self.llm: dict = {}
        self.prompt_cache: list[dict] = []

    def record(self, processor: str, model: str, usage: LLMTokenUsage) -> None:
        self.llm = merge_llm_usage(
            self.llm, {f"{processor}|||{model}": usage.model_dump(mode="json")}
        )

    def serialize(self) -> dict:
        return {"llm": self.llm, "prompt_cache": self.prompt_cache}


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
