"""Event-loop lag gauge — the per-pod saturation signal for autoscaling load tests.

The web pod runs a single uvicorn worker (one asyncio event loop, see
``scripts/run_web.sh``). When that loop is saturated it wakes *late* from
``asyncio.sleep``: a task that asked to sleep 100 ms actually resumes at
100 + X ms. That overshoot X is the cleanest provider-independent measure of how
close the pod is to its real ceiling — unlike CPU%, which is measured against the
2-core limit and reads ~50% at true saturation, and unlike turn latency, which is
dominated by external STT/LLM/TTS round-trips.

Phase 0 of the call-based autoscaling work (AUTOSCALING_PLAN.md) ramps concurrent
calls against one pod and reads this gauge to find the knee — the ``active_calls``
count where p95 lag climbs.

The gauge is a module global updated by one background task and read (peek) off
``GET /api/v1/health/active-calls``. Single event loop, so no lock is needed.
"""

import asyncio

# ponytail: single in-process gauge — exactly the unit (one event loop) we're
# sizing. A window of recent lag samples is enough for a p95; no metrics library.
_INTERVAL = 0.1  # seconds between probes
_WINDOW = 600  # ~60s of samples at 0.1s cadence
_samples: list[float] = []
# Strong ref to the monitor task: asyncio keeps only a weak reference, so
# without this the task can be garbage-collected mid-run and the gauge dies.
_task: "asyncio.Task | None" = None


async def _monitor() -> None:
    loop = asyncio.get_running_loop()
    while True:
        t0 = loop.time()
        await asyncio.sleep(_INTERVAL)
        lag_ms = (loop.time() - t0 - _INTERVAL) * 1000
        if lag_ms < 0:  # clock jitter; floor at 0
            lag_ms = 0.0
        _samples.append(lag_ms)
        if len(_samples) > _WINDOW:
            del _samples[: len(_samples) - _WINDOW]


def start() -> asyncio.Task:
    """Start the lag monitor on the running loop. Idempotent; call from lifespan."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_monitor())
    return _task


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    # nearest-rank; index clamped to the last element
    idx = min(len(ordered) - 1, int(pct / 100 * len(ordered)))
    return ordered[idx]


def stats() -> dict[str, float]:
    """Current lag over the recent window, in milliseconds. Read-only peek."""
    snapshot = list(_samples)
    return {
        "p95_ms": round(_percentile(snapshot, 95), 2),
        "max_ms": round(max(snapshot), 2) if snapshot else 0.0,
        "samples": len(snapshot),
    }


def demo() -> None:
    """Self-check: lag is ~0 when the loop is idle and spikes under a busy task."""

    async def _run():
        start()
        await asyncio.sleep(0.5)
        idle = stats()
        assert idle["p95_ms"] < 5, f"idle lag too high: {idle}"

        # Block the loop synchronously for ~150ms across several probe intervals.
        deadline = asyncio.get_running_loop().time() + 0.4
        while asyncio.get_running_loop().time() < deadline:
            end = asyncio.get_running_loop().time() + 0.15
            while asyncio.get_running_loop().time() < end:
                pass  # busy-spin, starves the loop
            await asyncio.sleep(0)
        busy = stats()
        assert busy["max_ms"] > 50, f"busy lag not detected: {busy}"
        print(f"OK  idle={idle}  busy={busy}")

    asyncio.run(_run())


if __name__ == "__main__":
    demo()
