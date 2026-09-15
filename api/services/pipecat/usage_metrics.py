"""Usage units exposed by voice providers outside Pipecat's token metrics."""

from pipecat.metrics.metrics import LLMTokenUsage, MetricsData


class LiveUsageMetricsData(MetricsData):
    """Additional seconds reported by a duration-billed Live session."""

    seconds: float


class SubscriptionVoiceUsageMetricsData(MetricsData):
    """Observed subscription voice usage without an inferred API price."""

    session_seconds: float
    input_audio_seconds: float
    output_audio_seconds: float
    session_state: str


class SubscriptionReasoningUsageMetricsData(MetricsData):
    """Observed subscription token use without a public API price."""

    value: LLMTokenUsage
