"""Integration tests for the Sakinah AI-to-AI simulation workflow.

The tests exercise the real route -> SimulationManager -> DB path (workflow
seeding, run creation with pinned definitions, event fan-out, watchdog,
mutual teardown, session persistence). The two per-agent pipelines are
replaced with lightweight fakes that behave like real ones:
- they emit rtf-bot-text / rtf-bot-stopped-speaking events through the
  ws-sender registry (exactly how RealtimeFeedbackObserver reports them), and
- they keep running until the transport's on_client_disconnected event fires,
  mirroring how a real pipeline ends when the simulation is stopped.
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from api.services.pipecat.ws_sender_registry import get_ws_sender
from api.services.quota_service import QuotaCheckResult
from api.services.sakinah.simulation import (
    SAKINAH_ROLE,
    SERVICE_USER_ROLE,
    SimulationAuthorizationError,
    simulation_manager,
)
from api.services.sakinah.workflow import (
    LEGACY_SAKINAH_START_PROMPTS,
    SAKINAH_WORKFLOW_DEFINITION,
    SERVICE_USER_WORKFLOW_NAME,
    WORKFLOW_NAME,
    ensure_sakinah_workflow,
)

SCENARIO = "You are Amina, a 34-year-old feeling overwhelmed at work."


async def _make_user(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    return await db_session.get_user_by_id(user.id)


async def _wait_for(predicate, timeout: float = 10.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met within timeout")


def _fake_pipeline(chunks_by_run: dict[int, list[str]] | None = None):
    """Build a fake _run_pipeline that streams events and waits for hangup."""

    async def fake_run_pipeline(
        transport, workflow_id, workflow_run_id, user_id, *args, **kwargs
    ):
        disconnected = asyncio.Event()

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(*_handler_args):
            disconnected.set()

        sender = get_ws_sender(workflow_run_id)
        assert sender is not None, "sender must be registered before pipelines"

        chunks = (chunks_by_run or {}).get(
            workflow_run_id, [f"chunk-{workflow_run_id}-a", f"chunk-{workflow_run_id}-b"]
        )
        for chunk in chunks:
            await sender({"type": "rtf-bot-text", "payload": {"text": chunk}})
        await sender({"type": "rtf-bot-stopped-speaking", "payload": {}})

        await disconnected.wait()

    return fake_run_pipeline


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("api.constants.SAKINAH_SESSIONS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def authorized_runs():
    with patch(
        "api.services.sakinah.simulation.authorize_workflow_run_start",
        new=AsyncMock(return_value=QuotaCheckResult(has_quota=True)),
    ) as mock_authorize:
        yield mock_authorize


@pytest.fixture
def no_start_delay(monkeypatch):
    # Keep the sakinah-first ordering but make tests fast and deterministic.
    monkeypatch.setattr(
        "api.services.sakinah.simulation.SERVICE_USER_START_DELAY_SECONDS", 0.2
    )


async def test_simulation_lifecycle_start_events_stop(
    test_client_factory, db_session, sessions_dir, authorized_runs, no_start_delay
):
    """Start seeds both workflows, streams labelled turns, and Stop finalizes."""
    user = await _make_user(db_session, "sim_lifecycle")

    with patch(
        "api.services.pipecat.run_pipeline._run_pipeline", new=_fake_pipeline()
    ):
        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/sakinah/simulations", json={"scenario": SCENARIO}
            )
            assert response.status_code == 200, response.text
            snapshot = response.json()

            simulation_id = snapshot["simulation_id"]
            assert snapshot["status"] == "running"
            assert set(snapshot["agents"]) == {SAKINAH_ROLE, SERVICE_USER_ROLE}

            # Both seeded workflows exist in the org, and each run pins the
            # published definition of its workflow.
            workflows = await db_session.get_all_workflows(
                organization_id=user.selected_organization_id
            )
            names = {workflow.name for workflow in workflows}
            assert {WORKFLOW_NAME, SERVICE_USER_WORKFLOW_NAME} <= names
            for role in (SAKINAH_ROLE, SERVICE_USER_ROLE):
                agent = snapshot["agents"][role]
                run = await db_session.get_workflow_run(
                    agent["workflow_run_id"],
                    organization_id=user.selected_organization_id,
                )
                assert run.workflow_id == agent["workflow_id"]
                assert run.definition_id is not None
                assert run.initial_context["scenario"] == SCENARIO
                assert run.initial_context["simulation_role"] == role

            # Turn-taking: only the service user is a listener; Sakinah must
            # speak first, the service user must wait for her greeting.
            sakinah_run = await db_session.get_workflow_run(
                snapshot["agents"][SAKINAH_ROLE]["workflow_run_id"],
                organization_id=user.selected_organization_id,
            )
            service_user_run = await db_session.get_workflow_run(
                snapshot["agents"][SERVICE_USER_ROLE]["workflow_run_id"],
                organization_id=user.selected_organization_id,
            )
            assert "suppress_initial_greeting" not in sakinah_run.initial_context
            assert service_user_run.initial_context["suppress_initial_greeting"] is True

            # Both authorizations were requested before launch.
            assert authorized_runs.await_count == 2

            simulation = simulation_manager.get(
                simulation_id, user.selected_organization_id
            )
            # Both fake pipelines emit their chunks; consecutive same-role
            # chunks aggregate into one turn per role.
            await _wait_for(lambda: simulation.turn_count >= 2)

            stop_response = await client.post(
                f"/api/v1/sakinah/simulations/{simulation_id}/stop"
            )
            assert stop_response.status_code == 200, stop_response.text
            stopped = stop_response.json()
            assert stopped["status"] == "completed"
            assert stopped["stop_reason"] == "user_stopped"

    # The merged, labelled transcript is saved as a session JSON.
    saved = json.loads(
        (Path(sessions_dir) / f"{simulation_id}.json").read_text()
    )
    roles = {turn["role"] for turn in saved["turns"]}
    assert roles == {SAKINAH_ROLE, SERVICE_USER_ROLE}
    sakinah_run = snapshot["agents"][SAKINAH_ROLE]["workflow_run_id"]
    sakinah_turn = next(t for t in saved["turns"] if t["role"] == SAKINAH_ROLE)
    assert sakinah_turn["text"] == f"chunk-{sakinah_run}-a chunk-{sakinah_run}-b"

    persisted_run = await db_session.get_sakinah_run(user.id, simulation_id)
    assert persisted_run is not None
    assert persisted_run.status == "completed"
    assert "assistant:" in persisted_run.transcript
    assert persisted_run.conversation
    assert persisted_run.preview_data["turns"]

    # Senders are unregistered after finalization.
    for role in (SAKINAH_ROLE, SERVICE_USER_ROLE):
        assert get_ws_sender(snapshot["agents"][role]["workflow_run_id"]) is None


async def test_peer_failure_closes_other_pipeline(
    test_client_factory, db_session, sessions_dir, authorized_runs, no_start_delay
):
    """When one pipeline fails, the other is shut down and the sim is failed."""
    user = await _make_user(db_session, "sim_peer_failure")

    healthy = _fake_pipeline()

    async def failing_or_healthy(
        transport, workflow_id, workflow_run_id, user_id, *args, **kwargs
    ):
        # The sakinah pipeline starts first (service user is delayed);
        # fail the first pipeline that runs.
        if not hasattr(failing_or_healthy, "_failed"):
            failing_or_healthy._failed = True
            raise RuntimeError("boom: TTS exploded")
        await healthy(transport, workflow_id, workflow_run_id, user_id, *args, **kwargs)

    with patch(
        "api.services.pipecat.run_pipeline._run_pipeline", new=failing_or_healthy
    ):
        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/sakinah/simulations", json={"scenario": SCENARIO}
            )
            assert response.status_code == 200, response.text
            simulation_id = response.json()["simulation_id"]

            simulation = simulation_manager.get(
                simulation_id, user.selected_organization_id
            )
            await _wait_for(lambda: simulation.status in ("completed", "failed"))

    assert simulation.status == "failed"
    assert simulation.stop_reason == "peer_failed"
    assert "boom: TTS exploded" in simulation.error
    # Both pipeline tasks ended.
    for agent in simulation.agents.values():
        assert agent.pipeline_task.done()
    # A pipeline-error event was published for the UI.
    assert any(e.get("type") == "pipeline-error" for e in simulation.events)


async def test_watchdog_stops_simulation_at_max_duration(
    db_session, sessions_dir, authorized_runs, no_start_delay
):
    """The conversation cannot continue indefinitely: the watchdog ends it."""
    user = await _make_user(db_session, "sim_watchdog")

    with patch(
        "api.services.pipecat.run_pipeline._run_pipeline", new=_fake_pipeline()
    ):
        simulation = await simulation_manager.start_simulation(
            user, SCENARIO, max_duration_seconds=1
        )
        await _wait_for(lambda: simulation.status in ("completed", "failed"))

    assert simulation.status == "completed"
    assert simulation.stop_reason == "max_duration"
    assert simulation.ended_at is not None
    # Finalization persisted the session JSON (regression: the watchdog must
    # not cancel itself before finalizing).
    assert (Path(sessions_dir) / f"{simulation.id}.json").exists()


async def test_start_rejected_when_run_authorization_fails(
    test_client_factory, db_session, sessions_dir, no_start_delay
):
    user = await _make_user(db_session, "sim_no_quota")

    with patch(
        "api.services.sakinah.simulation.authorize_workflow_run_start",
        new=AsyncMock(
            return_value=QuotaCheckResult(
                has_quota=False, error_message="Insufficient Dograh credits"
            )
        ),
    ):
        async with test_client_factory(user) as client:
            response = await client.post(
                "/api/v1/sakinah/simulations", json={"scenario": SCENARIO}
            )

    assert response.status_code == 402
    assert "Insufficient Dograh credits" in response.json()["detail"]


async def test_manager_raises_authorization_error(
    db_session, sessions_dir, no_start_delay
):
    user = await _make_user(db_session, "sim_auth_error")

    with patch(
        "api.services.sakinah.simulation.authorize_workflow_run_start",
        new=AsyncMock(return_value=QuotaCheckResult(has_quota=False)),
    ):
        with pytest.raises(SimulationAuthorizationError):
            await simulation_manager.start_simulation(user, SCENARIO)


def _definition_with_start_prompt(prompt: str) -> dict:
    import copy

    definition = copy.deepcopy(SAKINAH_WORKFLOW_DEFINITION)
    definition["nodes"][0]["data"]["prompt"] = prompt
    return definition


def _released_start_prompt(workflow) -> str:
    for node in workflow.released_definition.workflow_json["nodes"]:
        if node["type"] == "startCall":
            return node["data"]["prompt"]
    raise AssertionError("no startCall node")


async def test_stale_seed_prompt_upgraded(db_session):
    """A seed still carrying a superseded prompt is republished with the fix.

    Regression for the persona inversion: the old Sakinah prompt told her to
    'conduct the scenario', so a 'You are Amina...' scenario made Sakinah
    adopt the service user's persona.
    """
    user = await _make_user(db_session, "sim_stale_seed")
    organization_id = user.selected_organization_id
    created = await db_session.create_workflow(
        WORKFLOW_NAME,
        _definition_with_start_prompt(LEGACY_SAKINAH_START_PROMPTS[0]),
        user.id,
        organization_id,
    )
    workflow_id = created.id

    await ensure_sakinah_workflow(db_session, user)

    # The shared transactional test session caches loaded rows
    # (expire_on_commit=False); expire so the re-read sees the promoted
    # definition, as fresh per-request sessions do in production. Capture
    # plain ids first: expired ORM objects would lazy-load outside a
    # greenlet context.
    async with db_session.async_session() as session:
        session.expire_all()
    workflow = await db_session.get_workflow(
        workflow_id, organization_id=organization_id
    )
    new_prompt = SAKINAH_WORKFLOW_DEFINITION["nodes"][0]["data"]["prompt"]
    assert _released_start_prompt(workflow) == new_prompt


async def test_customized_seed_prompt_preserved(db_session):
    """A user-customized prompt is never overwritten by the seed upgrade."""
    user = await _make_user(db_session, "sim_custom_seed")
    custom_prompt = "My hand-tuned Sakinah prompt. {{scenario}}"
    await db_session.create_workflow(
        WORKFLOW_NAME,
        _definition_with_start_prompt(custom_prompt),
        user.id,
        user.selected_organization_id,
    )

    workflow = await ensure_sakinah_workflow(db_session, user)

    assert _released_start_prompt(workflow) == custom_prompt


def _capturing_event_handler(handlers: dict):
    def event_handler(name):
        def decorator(fn):
            handlers[name] = fn
            return fn

        return decorator

    return event_handler


@pytest.mark.parametrize("suppress_greeting", [False, True])
async def test_initial_greeting_gated_by_suppress_flag(suppress_greeting):
    """suppress_initial_greeting stops the pipeline from speaking first.

    The service-user side of an AI-to-AI simulation must stay silent until
    the peer's greeting arrives; normal runs still open the conversation.
    """
    from unittest.mock import MagicMock

    from api.services.pipecat.audio_config import AudioConfig
    from api.services.pipecat.event_handlers import register_event_handlers

    task_handlers: dict = {}
    transport_handlers: dict = {}

    task = MagicMock()
    task.event_handler = _capturing_event_handler(task_handlers)
    transport = MagicMock()
    transport.event_handler = _capturing_event_handler(transport_handlers)

    engine = MagicMock()
    engine._call_context_vars = (
        {"suppress_initial_greeting": True} if suppress_greeting else {}
    )
    engine.workflow.start_node_id = "start"
    engine.set_node = AsyncMock()
    engine.queue_node_opening = AsyncMock()

    audio_buffer = MagicMock()
    audio_buffer.start_recording = AsyncMock()

    register_event_handlers(
        task,
        transport,
        workflow_run_id=1,
        engine=engine,
        audio_buffer=audio_buffer,
        in_memory_logs_buffer=MagicMock(),
        transcript_log_coordinator=MagicMock(),
        pipeline_metrics_aggregator=MagicMock(),
        audio_config=AudioConfig(
            transport_in_sample_rate=16000, transport_out_sample_rate=16000
        ),
        user_provider_id="test",
    )

    await task_handlers["on_pipeline_started"](task, MagicMock())
    await transport_handlers["on_client_connected"](transport, None)

    engine.set_node.assert_awaited_once_with("start")
    if suppress_greeting:
        engine.queue_node_opening.assert_not_awaited()
    else:
        engine.queue_node_opening.assert_awaited_once()


async def test_simulation_is_org_scoped(
    test_client_factory, db_session, sessions_dir, authorized_runs, no_start_delay
):
    """A user from another organization cannot read or stop the simulation."""
    owner = await _make_user(db_session, "sim_owner")
    outsider = await _make_user(db_session, "sim_outsider")

    with patch(
        "api.services.pipecat.run_pipeline._run_pipeline", new=_fake_pipeline()
    ):
        async with test_client_factory(owner) as client:
            response = await client.post(
                "/api/v1/sakinah/simulations", json={"scenario": SCENARIO}
            )
            assert response.status_code == 200, response.text
            simulation_id = response.json()["simulation_id"]

        async with test_client_factory(outsider) as client:
            assert (
                await client.get(f"/api/v1/sakinah/simulations/{simulation_id}")
            ).status_code == 404
            assert (
                await client.post(f"/api/v1/sakinah/simulations/{simulation_id}/stop")
            ).status_code == 404

        # The owner can still stop it (cleanup for the shared event loop).
        async with test_client_factory(owner) as client:
            stop_response = await client.post(
                f"/api/v1/sakinah/simulations/{simulation_id}/stop"
            )
            assert stop_response.status_code == 200
