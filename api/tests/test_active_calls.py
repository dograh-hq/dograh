"""Unit tests for the per-worker active-call registry (deploy draining).

The registry backs GET /api/v1/health/active-calls, which scripts/rolling_update.sh
(and a k8s preStop hook) polls to wait for live calls to finish before stopping a
worker. The guarantees that matter for draining: register/unregister are
idempotent, and the count only reaches zero when every registered run is gone.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import main as main_routes
from api.services.pipecat import active_calls
from api.services.pipecat import run_pipeline as run_pipeline_module


def setup_function():
    # Module-level state — start each test from an empty registry.
    active_calls._active_run_ids.clear()


def _make_active_calls_client(
    monkeypatch,
    configured_secret: str | None = "test-dograh-devops-secret",
) -> TestClient:
    monkeypatch.setattr("api.constants.DOGRAH_DEVOPS_SECRET", configured_secret)
    app = FastAPI()
    app.add_api_route(
        "/api/v1/health/active-calls",
        main_routes.active_calls,
        methods=["GET"],
        response_model=main_routes.ActiveCallsResponse,
    )
    return TestClient(app)


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


@pytest.mark.asyncio
async def test_run_pipeline_counts_call_during_setup(monkeypatch):
    entered_setup = asyncio.Event()
    release_setup = asyncio.Event()

    async def fake_get_workflow_run(*args, **kwargs):
        entered_setup.set()
        await release_setup.wait()
        raise RuntimeError("setup failed")

    monkeypatch.setattr(
        run_pipeline_module.db_client,
        "get_workflow_run",
        fake_get_workflow_run,
    )

    task = asyncio.create_task(
        run_pipeline_module._run_pipeline(
            transport=object(),
            workflow_id=1,
            workflow_run_id=42,
            user_id=7,
        )
    )

    await asyncio.wait_for(entered_setup.wait(), timeout=1.0)
    assert active_calls.active_call_count() == 1

    release_setup.set()
    with pytest.raises(RuntimeError, match="setup failed"):
        await asyncio.wait_for(task, timeout=1.0)
    assert active_calls.active_call_count() == 0


@pytest.mark.asyncio
async def test_webrtc_entrypoint_counts_call_during_setup(monkeypatch):
    entered_setup = asyncio.Event()
    release_setup = asyncio.Event()

    async def fake_get_workflow(*args, **kwargs):
        entered_setup.set()
        await release_setup.wait()
        raise RuntimeError("setup failed")

    monkeypatch.setattr(
        run_pipeline_module.db_client, "get_workflow", fake_get_workflow
    )

    task = asyncio.create_task(
        run_pipeline_module.run_pipeline_smallwebrtc(
            webrtc_connection=object(),
            workflow_id=1,
            workflow_run_id=43,
            user_id=7,
        )
    )

    await asyncio.wait_for(entered_setup.wait(), timeout=1.0)
    assert active_calls.active_call_count() == 1

    release_setup.set()
    with pytest.raises(RuntimeError, match="setup failed"):
        await asyncio.wait_for(task, timeout=1.0)
    assert active_calls.active_call_count() == 0


@pytest.mark.asyncio
async def test_telephony_entrypoint_counts_call_during_setup(monkeypatch):
    entered_setup = asyncio.Event()
    release_setup = asyncio.Event()

    async def fake_get_workflow(*args, **kwargs):
        entered_setup.set()
        await release_setup.wait()
        raise RuntimeError("setup failed")

    monkeypatch.setattr(
        run_pipeline_module.db_client, "get_workflow", fake_get_workflow
    )

    task = asyncio.create_task(
        run_pipeline_module.run_pipeline_telephony(
            websocket=object(),
            provider_name="twilio",
            workflow_id=1,
            workflow_run_id=44,
            user_id=7,
            call_id="call-1",
            transport_kwargs={},
        )
    )

    await asyncio.wait_for(entered_setup.wait(), timeout=1.0)
    assert active_calls.active_call_count() == 1

    release_setup.set()
    with pytest.raises(RuntimeError, match="setup failed"):
        await asyncio.wait_for(task, timeout=1.0)
    assert active_calls.active_call_count() == 0


def test_active_calls_route_requires_configured_secret(monkeypatch):
    client = _make_active_calls_client(monkeypatch, configured_secret=None)

    response = client.get(
        "/api/v1/health/active-calls",
        headers={"X-Dograh-Devops-Secret": "test-dograh-devops-secret"},
    )

    assert response.status_code == 503


def test_active_calls_route_rejects_missing_secret_header(monkeypatch):
    client = _make_active_calls_client(monkeypatch)

    response = client.get("/api/v1/health/active-calls")

    assert response.status_code == 403


def test_active_calls_route_rejects_wrong_secret(monkeypatch):
    client = _make_active_calls_client(monkeypatch)

    response = client.get(
        "/api/v1/health/active-calls",
        headers={"X-Dograh-Devops-Secret": "wrong"},
    )

    assert response.status_code == 403


def test_active_calls_route_returns_count_with_secret(monkeypatch):
    active_calls.register_active_call(42)
    client = _make_active_calls_client(monkeypatch)

    response = client.get(
        "/api/v1/health/active-calls",
        headers={"X-Dograh-Devops-Secret": "test-dograh-devops-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"active_calls": 1}
