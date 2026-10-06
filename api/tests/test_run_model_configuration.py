"""Run setup must authorize and execute the same final configuration."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.services.configuration import model_connections as catalog_service
from api.services.configuration import run_model_configuration as service
from api.services.pipecat.pre_call_fetch import PreCallFetchResult
from api.tests.test_model_connections import catalog as catalog_fixture
from api.tests.test_model_connections import connection

catalog = catalog_fixture


@pytest.fixture
def run_state(monkeypatch):
    state = SimpleNamespace(
        id=51,
        workflow_id=7,
        mode="textchat",
        call_type="outbound",
        initial_context={"customer": "before"},
        model_configuration_snapshot=None,
        model_configuration_overrides={"llm": {"settings": {"temperature": 0.3}}},
        definition=SimpleNamespace(
            workflow_configurations={
                "model_configuration_override": {
                    "llm": {"settings": {"temperature": 0.1}}
                }
            },
            workflow_json={"nodes": []},
        ),
    )

    async def claim(run_id, org_id):
        assert (run_id, org_id) == (51, 1)
        if state.model_configuration_snapshot is not None:
            return None
        state.model_configuration_snapshot = {
            "preparation_state": "preparing",
            "preparation_owner": "owner",
        }
        return "owner"

    async def finish(run_id, org_id, owner, *, snapshot, initial_context_patch=None):
        assert (run_id, org_id, owner) == (51, 1, "owner")
        state.model_configuration_snapshot = deepcopy(snapshot)
        state.initial_context.update(initial_context_patch or {})
        return True

    monkeypatch.setattr(
        service.db_client, "claim_run_model_preparation", AsyncMock(side_effect=claim)
    )
    monkeypatch.setattr(
        service.db_client, "finish_run_model_preparation", AsyncMock(side_effect=finish)
    )
    monkeypatch.setattr(
        service.db_client,
        "get_workflow_run",
        AsyncMock(side_effect=lambda *a, **kw: deepcopy(state)),
    )
    monkeypatch.setattr(
        service,
        "_fetch_for_run",
        AsyncMock(
            return_value=PreCallFetchResult(
                initial_context={
                    "customer": "after",
                    "mps_correlation_id": "untrusted",
                },
                model_overrides={"llm": {"settings": {"temperature": 0.6}}},
                outcome="completed",
            )
        ),
    )
    return state


@pytest.mark.asyncio
async def test_pre_call_wins_and_retry_hydrates_same_key_without_fetch(
    catalog, run_state
):
    row = connection(keys=["secret-a", "secret-b"])
    catalog(row, default=True)
    first = await service.prepare_run_model_configuration(
        organization_id=1, workflow_run=deepcopy(run_state)
    )
    assert first.llm.temperature == 0.6
    assert first.llm.api_key == first.tts.api_key == first.stt.api_key
    snapshot = run_state.model_configuration_snapshot
    assert snapshot["preparation_state"] == "ready"
    assert "secret-" not in str(snapshot)
    assert run_state.initial_context == {"customer": "after"}

    row.is_active = False
    retried = await service.prepare_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    runtime = await service.get_effective_ai_model_configuration_for_run(
        organization_id=1, workflow_run=run_state
    )
    assert retried.llm.api_key == runtime.llm.api_key == first.llm.api_key
    service._fetch_for_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_preparation_fetches_only_once(
    catalog, run_state, monkeypatch
):
    catalog(connection(), default=True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(*args):
        entered.set()
        await release.wait()
        return PreCallFetchResult(outcome="completed")

    fetch_mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(service, "_fetch_for_run", fetch_mock)
    first = asyncio.create_task(
        service.prepare_run_model_configuration(
            organization_id=1, workflow_run=deepcopy(run_state)
        )
    )
    await entered.wait()
    second = asyncio.create_task(
        service.prepare_run_model_configuration(
            organization_id=1, workflow_run=deepcopy(run_state)
        )
    )
    release.set()
    a, b = await asyncio.gather(first, second)
    assert a.llm.api_key == b.llm.api_key
    fetch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_explicit_override_is_terminal_and_secret_free(
    catalog, run_state, monkeypatch
):
    catalog(connection(), default=True)
    monkeypatch.setattr(
        service,
        "_fetch_for_run",
        AsyncMock(
            return_value=PreCallFetchResult(
                model_overrides={"llm": {"settings": {"api_key": "must-not-leak"}}},
                outcome="completed",
            )
        ),
    )
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            await service.prepare_run_model_configuration(
                organization_id=1, workflow_run=deepcopy(run_state)
            )
        assert error.value.status_code == 422
        assert "must-not-leak" not in str(error.value)
    assert run_state.model_configuration_snapshot["preparation_state"] == "failed"
    assert "must-not-leak" not in str(run_state.model_configuration_snapshot)
    service._fetch_for_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_unavailable_fetch_uses_api_patch_and_caches_outcome(
    catalog, run_state, monkeypatch
):
    catalog(connection(), default=True)
    monkeypatch.setattr(
        service, "_fetch_for_run", AsyncMock(return_value=PreCallFetchResult())
    )
    effective = await service.prepare_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    assert effective.llm.temperature == 0.3
    assert (
        run_state.model_configuration_snapshot["pre_call_fetch_outcome"]
        == "unavailable"
    )


@pytest.mark.asyncio
async def test_pending_snapshot_cannot_fall_back_to_mutable_configuration(run_state):
    run_state.model_configuration_snapshot = {"preparation_state": "preparing"}
    with pytest.raises(HTTPException) as error:
        await service.get_effective_ai_model_configuration_for_run(
            organization_id=1, workflow_run=run_state
        )
    assert error.value.status_code == 409


def test_prepared_fetch_context_wins_over_stale_start_request(run_state):
    run_state.initial_context = {
        "customer": "fetched",
        "mps_correlation_id": "authorized",
    }
    run_state.model_configuration_snapshot = {"preparation_state": "ready"}
    context = service.merge_run_start_context(
        run_state,
        {
            "customer": "original",
            "new_variable": "extra",
            "mps_correlation_id": "forged",
        },
    )
    assert context == {
        "customer": "fetched",
        "new_variable": "extra",
        "mps_correlation_id": "authorized",
    }


@pytest.mark.asyncio
async def test_unmigrated_org_keeps_legacy_runtime(catalog, run_state):
    run_state.definition.workflow_configurations = {}
    run_state.model_configuration_overrides = None
    assert (
        await service.prepare_run_model_configuration(
            organization_id=1, workflow_run=run_state
        )
        is None
    )
    service._fetch_for_run.assert_not_awaited()
    service.db_client.claim_run_model_preparation.assert_not_awaited()


@pytest.mark.asyncio
async def test_embedding_check_uses_documents_from_pinned_definition(
    catalog, run_state, monkeypatch
):
    catalog(connection(), default=True)
    run_state.definition.workflow_json = {
        "nodes": [{"data": {"document_uuids": ["doc-a"]}}]
    }
    validate = AsyncMock(
        side_effect=HTTPException(
            status_code=422, detail="Incompatible embedding model"
        )
    )
    monkeypatch.setattr(catalog_service, "validate_embedding_compatibility", validate)
    with pytest.raises(HTTPException):
        await service.prepare_run_model_configuration(
            organization_id=1, workflow_run=run_state
        )
    assert validate.await_args.args[2] == ["doc-a"]
    assert run_state.model_configuration_snapshot["preparation_state"] == "failed"
