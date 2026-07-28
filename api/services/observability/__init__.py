"""Per-process runtime observability signals, surfaced on the ops health
endpoints (/api/v1/health/*).

``active_calls.py`` — in-process registry of live calls; the drain signal for
deploys and scale-down. ``loop_lag.py`` — event-loop lag gauge; the per-pod
saturation signal for autoscaling load tests. Neither is domain logic: modules
here observe the running process, they don't drive calls.
"""
