"""In-process registry of active pipeline runs (live voice calls).

Each uvicorn worker tracks the calls it is currently running so a deploy
orchestrator can *drain* the worker before stopping it: poll the count, wait for
zero, then send SIGTERM. Sending SIGTERM while calls are live makes uvicorn
force-close their WebSockets (close code 1012), which cuts the calls instead of
letting them finish — so the wait has to happen first.

The registry is deliberately per-process. That is exactly the unit that gets
drained: one uvicorn process per VM port (see ``scripts/rolling_update.sh``) or
one uvicorn process per Kubernetes pod (drained via a ``preStop`` hook). The
count is exposed read-only at ``GET /api/v1/health/active-calls`` and is also a
natural autoscaling signal (concurrent calls per worker).

Access is single-threaded (asyncio event loop), so no lock is needed. A set of
run ids — rather than a bare counter — keeps register/unregister idempotent and
makes the in-flight runs inspectable for debugging.
"""

_active_run_ids: set[int] = set()


def register_active_call(workflow_run_id: int) -> None:
    """Mark a pipeline run as active in this worker."""
    _active_run_ids.add(workflow_run_id)


def unregister_active_call(workflow_run_id: int) -> None:
    """Mark a pipeline run as finished in this worker."""
    _active_run_ids.discard(workflow_run_id)


def active_call_count() -> int:
    """Number of pipeline runs currently active in this worker."""
    return len(_active_run_ids)
