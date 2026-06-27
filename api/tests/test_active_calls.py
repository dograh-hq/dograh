"""Unit tests for the per-worker active-call registry (deploy draining).

The registry backs GET /api/v1/health/active-calls, which scripts/rolling_update.sh
(and a k8s preStop hook) polls to wait for live calls to finish before stopping a
worker. The guarantees that matter for draining: register/unregister are
idempotent, and the count only reaches zero when every registered run is gone.
"""

from api.services.pipecat import active_calls


def setup_function():
    # Module-level state — start each test from an empty registry.
    active_calls._active_run_ids.clear()


def test_starts_empty():
    assert active_calls.active_call_count() == 0


def test_register_counts_distinct_runs():
    active_calls.register_active_call(1)
    active_calls.register_active_call(2)
    assert active_calls.active_call_count() == 2


def test_register_is_idempotent():
    # Registering the same run twice must not double-count, or the count could
    # never drain to zero.
    active_calls.register_active_call(1)
    active_calls.register_active_call(1)
    assert active_calls.active_call_count() == 1


def test_unregister_removes_run():
    active_calls.register_active_call(1)
    active_calls.register_active_call(2)
    active_calls.unregister_active_call(1)
    assert active_calls.active_call_count() == 1


def test_unregister_unknown_run_is_a_noop():
    # discard() semantics: unregistering a run that was never registered (or was
    # already removed) is safe and cannot push the count negative.
    active_calls.unregister_active_call(999)
    assert active_calls.active_call_count() == 0


def test_full_lifecycle_drains_to_zero():
    active_calls.register_active_call(42)
    assert active_calls.active_call_count() == 1
    active_calls.unregister_active_call(42)
    assert active_calls.active_call_count() == 0
