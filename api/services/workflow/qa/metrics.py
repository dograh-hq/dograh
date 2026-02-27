"""Call metrics computation from raw event logs."""

from pipecat.utils.enums import RealtimeFeedbackType


def compute_call_metrics(
    logs: list[dict], call_duration_seconds: float | None = None
) -> dict:
    """Pre-compute quantitative metrics from raw call logs."""
    latencies = []
    ttfb_values = []

    for event in logs:
        if event["type"] == RealtimeFeedbackType.LATENCY_MEASURED.value:
            latencies.append(event["payload"]["latency_seconds"])
        elif event["type"] == RealtimeFeedbackType.TTFB_METRIC.value:
            ttfb_values.append(event["payload"]["ttfb_seconds"])

    turns = set()
    for event in logs:
        if event["type"] in (
            RealtimeFeedbackType.USER_TRANSCRIPTION.value,
            RealtimeFeedbackType.BOT_TEXT.value,
        ):
            turns.add(event.get("turn", 0))

    return {
        "call_duration_seconds": call_duration_seconds,
        "num_turns": len(turns),
        "avg_latency_seconds": (
            round(sum(latencies) / len(latencies), 2) if latencies else None
        ),
        "avg_ttfb_seconds": (
            round(sum(ttfb_values) / len(ttfb_values), 2) if ttfb_values else None
        ),
        "max_latency_seconds": round(max(latencies), 2) if latencies else None,
    }
