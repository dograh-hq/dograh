"""Per-process runtime observability signals, surfaced on the ops health
endpoints (/api/v1/health/*).

``active_calls.py`` tracks live voice pipelines for draining and scaling.
``loop_lag.py`` samples event-loop saturation for the health endpoints.
``metrics.py`` exports concurrent calls, user-to-bot latency, and AI service
latency through OpenTelemetry's Prometheus reader; ``pipeline_metrics.py``
attaches the Pipecat observers. These modules
observe the running process, they don't drive calls.
"""
