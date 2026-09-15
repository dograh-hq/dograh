"""Usage units exposed by voice providers outside Pipecat's token metrics."""

from pipecat.metrics.metrics import MetricsData


class LiveUsageMetricsData(MetricsData):
    """Additional seconds reported by a duration-billed Live session."""

    seconds: float
