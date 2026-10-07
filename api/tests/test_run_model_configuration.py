"""Call-scoped setup is pinned before connect; the hook patches visit services after."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.services.configuration import model_connections as catalog_service
from api.services.configuration import run_model_configuration as service
from api.services.pipecat.pre_call_fetch import PreCallFetchResult
from api.tests.test_model_connections import catalog as catalog_fixture
from api.tests.test_model_connections import connection, pipeline

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

    async def store_if_absent(run_id, org_id, snapshot):
        assert (run_id, org_id) == (51, 1)
        if state.model_configuration_snapshot is None:
            state.model_configuration_snapshot = deepcopy(snapshot)
        return state.model_configuration_snapshot

    async def update(run_id, **kwargs):
        assert run_id == 51
        if kwargs.get("model_configuration_snapshot"):
            state.model_configuration_snapshot = deepcopy(
                kwargs["model_configuration_snapshot"]
            )

    monkeypatch.setattr(
        service.db_client,
        "store_model_configuration_snapshot_if_absent",
        AsyncMock(side_effect=store_if_absent),
    )
    monkeypatch.setattr(
        service.db_client, "update_workflow_run", AsyncMock(side_effect=update)
    )
    return state


@pytest.fixture
def failures(monkeypatch):
    log = Mock()
    monkeypatch.setattr(service, "log_failure", log)
    return log


@pytest.mark.asyncio
async def test_resolve_pins_api_over_workflow_once_and_reuses_the_pin(
    catalog, run_state
):
    row = connection(keys=["secret-a", "secret-b"])
    catalog(row, default=True)
    first = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    assert first.llm.temperature == 0.3
    assert first.llm.api_key == first.tts.api_key == first.stt.api_key
    snapshot = run_state.model_configuration_snapshot
    assert snapshot["version"] == 3
    assert "preparation_state" not in snapshot
    assert "secret-" not in str(snapshot)

    # A retry, even after the connection is archived, executes the pinned
    # choice rather than resolving again.
    row.is_active = False
    again = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    runtime = await service.get_effective_ai_model_configuration_for_run(
        organization_id=1, workflow_run=run_state
    )
    assert again.llm.api_key == runtime.llm.api_key == first.llm.api_key
    service.db_client.store_model_configuration_snapshot_if_absent.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_resolution_executes_whatever_was_stored_first(
    catalog, run_state
):
    catalog(connection(keys=["secret-a", "secret-b"]), default=True)
    stored = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=deepcopy(run_state)
    )
    # This caller resolved on its own but lost the race to store.
    late = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=deepcopy(run_state)
    )
    assert late.llm.api_key == stored.llm.api_key


@pytest.mark.asyncio
async def test_invalid_run_configuration_fails_authorization_without_pinning(
    catalog, run_state
):
    catalog(connection(), default=True)
    run_state.model_configuration_overrides = {
        "llm": {"settings": {"api_key": "must-not-leak"}}
    }
    with pytest.raises(HTTPException) as error:
        await service.resolve_run_model_configuration(
            organization_id=1, workflow_run=run_state
        )
    assert error.value.status_code == 422
    assert "must-not-leak" not in str(error.value)
    assert run_state.model_configuration_snapshot is None


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
        await service.resolve_run_model_configuration(
            organization_id=1, workflow_run=run_state
        )
    assert validate.await_args.args[2] == ["doc-a"]
    assert run_state.model_configuration_snapshot is None


@pytest.mark.asyncio
async def test_unmigrated_org_is_bootstrapped_before_resolving(
    catalog, run_state, monkeypatch
):
    bootstrap = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "api.services.configuration.model_configuration_migration."
        "ensure_organization_model_catalog",
        bootstrap,
    )
    # Nothing to import either, so the run has no models at all.
    with pytest.raises(HTTPException) as error:
        await service.resolve_run_model_configuration(
            organization_id=1, workflow_run=run_state
        )
    assert error.value.status_code == 422
    bootstrap.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_pre_call_overrides_patch_visit_services_within_the_run_key(
    catalog, run_state, failures
):
    catalog(connection(keys=["secret-a", "secret-b"]), default=True)
    pinned = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    patched = await service.apply_pre_call_model_overrides(
        organization_id=1,
        workflow_run=run_state,
        run_model_configuration=pinned,
        fetched=PreCallFetchResult(
            model_overrides={
                "llm": {"settings": {"temperature": 0.6}},
                "tts": {"settings": {"voice": "Alice"}},
            },
            outcome="completed",
        ),
    )
    assert patched.llm.temperature == 0.6
    assert patched.tts.voice == "Alice"
    assert patched.llm.api_key == patched.tts.api_key == pinned.llm.api_key
    assert patched.stt.provider == pinned.stt.provider
    snapshot = run_state.model_configuration_snapshot
    assert snapshot["services"]["tts"]["settings"]["voice"] == "Alice"
    assert "secret-" not in str(snapshot)
    assert [entry["source"] for entry in snapshot["provenance"]][-1] == "pre_call"
    failures.assert_not_called()


@pytest.mark.asyncio
async def test_pre_call_without_overrides_changes_nothing(catalog, run_state):
    catalog(connection(), default=True)
    pinned = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    assert (
        await service.apply_pre_call_model_overrides(
            organization_id=1,
            workflow_run=run_state,
            run_model_configuration=pinned,
            fetched=PreCallFetchResult(outcome="completed"),
        )
        is None
    )
    service.db_client.update_workflow_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        "invalid",
        {"stt": {"settings": {"model": "other"}}},
        {"mode": "realtime"},
        {"realtime": {"settings": {}}},
        {"embeddings": None},
        {"model_configuration_uuid": "11111111-1111-4111-8111-111111111111"},
        {"llm": {"settings": {"api_key": "must-not-leak"}}},
        {"llm": None},
    ],
    ids=[
        "not-an-object",
        "stt",
        "mode",
        "realtime",
        "embeddings",
        "named-configuration",
        "credential-in-settings",
        "null-service",
    ],
)
async def test_pre_call_overrides_outside_the_visit_are_rejected_not_fatal(
    catalog, run_state, failures, overrides
):
    catalog(connection(), default=True)
    pinned = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    before = deepcopy(run_state.model_configuration_snapshot)
    result = await service.apply_pre_call_model_overrides(
        organization_id=1,
        workflow_run=run_state,
        run_model_configuration=pinned,
        fetched=PreCallFetchResult(model_overrides=overrides, outcome="completed"),
    )
    assert result is None
    assert run_state.model_configuration_snapshot == before
    service.db_client.update_workflow_run.assert_not_awaited()
    failure = failures.call_args.args[0]
    assert failure.code == "pre-call-model-overrides-rejected"
    assert "must-not-leak" not in str(failure.internal_message)
    assert failures.call_args.kwargs["workflow_run_id"] == 51


@pytest.mark.asyncio
async def test_pre_call_overrides_cannot_switch_the_dograh_key(
    catalog, run_state, failures
):
    catalog(connection(keys="root-key"), default=True)
    unrelated = catalog(connection(keys="unrelated-key"))
    pinned = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    result = await service.apply_pre_call_model_overrides(
        organization_id=1,
        workflow_run=run_state,
        run_model_configuration=pinned,
        fetched=PreCallFetchResult(
            model_overrides={"llm": {"provider_connection_uuid": unrelated.uuid}},
            outcome="completed",
        ),
    )
    assert result is None
    assert "unrelated-key" not in str(failures.call_args)
    assert failures.call_args.args[0].code == "pre-call-model-overrides-rejected"


@pytest.mark.asyncio
async def test_pre_call_override_can_pick_a_connection_sharing_the_run_key(
    catalog, run_state
):
    root = connection(keys="shared")
    other = catalog(connection(keys=["other-key", "shared"]))
    catalog(root, default=True, configuration=pipeline(root))
    pinned = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    patched = await service.apply_pre_call_model_overrides(
        organization_id=1,
        workflow_run=run_state,
        run_model_configuration=pinned,
        fetched=PreCallFetchResult(
            model_overrides={"tts": {"provider_connection_uuid": other.uuid}},
            outcome="completed",
        ),
    )
    assert patched is not None
    assert patched.tts.api_key == pinned.llm.api_key == "shared"


@pytest.mark.asyncio
async def test_pre_call_uses_the_pin_after_catalog_edits_and_archive(
    catalog, run_state
):
    row = connection("openai", keys=["key-a", "key-b"])
    original = catalog(row, default=True, configuration=pipeline(row))
    pinned = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    before = deepcopy(run_state.model_configuration_snapshot)
    original.configuration["stt"]["settings"]["model"] = "changed-stt"
    original.configuration["tts"]["settings"]["voice"] = "changed-voice"
    row.connection_settings = {"base_url": "https://changed.example.com/v1"}
    row.is_active = False
    catalog(connection(), default=True)

    patched = await service.apply_pre_call_model_overrides(
        organization_id=1,
        workflow_run=run_state,
        run_model_configuration=pinned,
        fetched=PreCallFetchResult(
            model_overrides={"llm": {"settings": {"temperature": 0.6}}},
            outcome="completed",
        ),
    )
    assert patched is not None
    assert patched.llm.temperature == 0.6
    assert patched.stt == pinned.stt
    assert patched.tts == pinned.tts
    assert patched.llm.api_key == pinned.llm.api_key
    assert patched.llm.base_url == pinned.llm.base_url
    assert (
        run_state.model_configuration_snapshot["services"]["stt"]
        == before["services"]["stt"]
    )
    hydrated = await catalog_service.hydrate_model_configuration_snapshot(
        1, run_state.model_configuration_snapshot
    )
    assert hydrated.llm.api_key == pinned.llm.api_key


@pytest.mark.asyncio
async def test_pre_call_cannot_add_managed_services_to_an_authorized_byok_run(
    catalog, run_state, failures
):
    catalog(connection("openai"), default=True)
    dograh = catalog(connection())
    pinned = await service.resolve_run_model_configuration(
        organization_id=1, workflow_run=run_state
    )
    before = deepcopy(run_state.model_configuration_snapshot)
    patched = await service.apply_pre_call_model_overrides(
        organization_id=1,
        workflow_run=run_state,
        run_model_configuration=pinned,
        fetched=PreCallFetchResult(
            model_overrides={"tts": {"provider_connection_uuid": dograh.uuid}},
            outcome="completed",
        ),
    )
    assert patched is None
    assert run_state.model_configuration_snapshot == before
    assert failures.call_args.args[0].code == "pre-call-model-overrides-rejected"
