"""Behavioral tests for catalog precedence, tenant isolation and credential pins."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.schemas.model_connections import ModelConfigurationOverride
from api.services.configuration import model_connections as service


def connection(provider="dograh", keys=None, **kwargs):
    return SimpleNamespace(
        uuid=str(uuid4()),
        provider=provider,
        credentials={"api_key": keys or "secret"},
        connection_settings={},
        revision=1,
        organization_id=1,
        is_active=True,
        **kwargs,
    )


def selection(row, **settings):
    return {"provider_connection_uuid": row.uuid, "settings": settings}


def pipeline(row):
    return {
        "version": 3,
        "mode": "pipeline",
        **{role: selection(row) for role in ("llm", "stt", "tts")},
    }


@pytest.fixture
def catalog(monkeypatch):
    rows = {}
    configurations = {}
    pointer = {"uuid": None}

    async def get_connection(org, uuid, active_only=True):
        row = rows.get(uuid)
        if row and row.organization_id == org and (row.is_active or not active_only):
            return row
        return None

    async def get_named(org, uuid):
        row = configurations.get(uuid)
        return row if row and row.organization_id == org else None

    async def get_default(org, key):
        return SimpleNamespace(value=pointer["uuid"]) if pointer["uuid"] else None

    monkeypatch.setattr(
        service.db_client,
        "get_provider_connection",
        AsyncMock(side_effect=get_connection),
    )
    monkeypatch.setattr(
        service.db_client,
        "get_named_model_configuration",
        AsyncMock(side_effect=get_named),
    )
    monkeypatch.setattr(
        service.db_client, "get_configuration", AsyncMock(side_effect=get_default)
    )

    def add(row, default=False, configuration=None):
        rows[row.uuid] = row
        if default or configuration:
            named = SimpleNamespace(
                uuid=str(uuid4()),
                organization_id=1,
                configuration=configuration or pipeline(row),
                revision=1,
            )
            configurations[named.uuid] = named
            if default:
                pointer["uuid"] = named.uuid
            return named
        return row

    return add


@pytest.mark.asyncio
async def test_four_layers_and_named_reset(catalog):
    dograh = connection()
    catalog(dograh, default=True)
    other = pipeline(dograh)
    other["llm"]["settings"] = {"temperature": 0.7}
    other["tts"]["settings"] = {"voice": "Bob"}
    named = catalog(dograh, configuration=other)
    result = await service.resolve_model_configuration(
        1,
        workflow_override={"llm": {"settings": {"temperature": 0.4}}},
        api_override={"model_configuration_uuid": named.uuid},
        pre_call_override={"tts": {"settings": {"voice": "Alice"}}},
    )
    assert result.effective.llm.temperature == 0.7
    assert result.effective.tts.voice == "Alice"
    assert [entry["source"] for entry in result.provenance] == [
        "organization",
        "workflow",
        "api",
        "pre_call",
    ]


@pytest.mark.asyncio
async def test_same_provider_connection_preserves_settings(catalog):
    dograh = connection()
    config = pipeline(dograh)
    config["llm"]["settings"] = {"temperature": 0.3, "model": "saved-tier"}
    catalog(dograh, default=True, configuration=config)
    second = catalog(connection())
    result = await service.resolve_model_configuration(
        1, api_override={"llm": {"provider_connection_uuid": second.uuid}}
    )
    assert result.effective.llm.temperature == 0.3
    assert result.effective.llm.model == "saved-tier"


@pytest.mark.asyncio
async def test_new_provider_drops_previous_provider_settings(catalog):
    dograh = connection()
    config = pipeline(dograh)
    config["llm"]["settings"] = {"temperature": 0.3, "model": "saved-tier"}
    catalog(dograh, default=True, configuration=config)
    google = catalog(connection("google"))
    result = await service.resolve_model_configuration(
        1,
        api_override={
            "llm": {
                "provider_connection_uuid": google.uuid,
                "settings": {"temperature": 0.8},
            }
        },
    )
    assert result.effective.llm.temperature == 0.8
    assert result.effective.llm.model != "saved-tier"
    assert result.effective.tts.provider == "dograh"


@pytest.mark.asyncio
async def test_null_temperature_and_embeddings_are_preserved(catalog):
    row = connection()
    config = pipeline(row)
    config["embeddings"] = selection(row)
    catalog(row, default=True, configuration=config)
    result = await service.resolve_model_configuration(
        1, api_override={"embeddings": None, "llm": {"settings": {"temperature": None}}}
    )
    assert result.effective.embeddings is None
    assert result.snapshot["services"]["llm"]["settings"]["temperature"] is None


@pytest.mark.parametrize(
    "patch",
    [
        {"llm": None},
        {"mode": None},
        {"model_configuration_uuid": None},
        {"tts": {"provider_connection_uuid": None}},
    ],
)
def test_null_required_reference_is_rejected(patch):
    with pytest.raises(ValidationError):
        ModelConfigurationOverride.model_validate(patch)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        {"api_key": "must-not-leak"},
        {"base_url": "https://evil.example"},
        {"provider": "openai"},
        {"nonsense": 1},
    ],
)
async def test_override_cannot_change_secrets_connections_or_unknown_fields(
    catalog, settings
):
    catalog(connection(), default=True)
    with pytest.raises(HTTPException) as caught:
        await service.resolve_model_configuration(
            1, api_override={"llm": {"settings": settings}}
        )
    assert caught.value.status_code == 422
    assert "must-not-leak" not in str(caught.value)


@pytest.mark.asyncio
async def test_connection_reference_is_organization_scoped(catalog):
    catalog(connection(), default=True)
    outside = catalog(connection())
    outside.organization_id = 2
    with pytest.raises(HTTPException) as caught:
        await service.resolve_model_configuration(
            1, api_override={"llm": {"provider_connection_uuid": outside.uuid}}
        )
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_named_reference_is_organization_scoped(catalog):
    catalog(connection(), default=True)
    named = catalog(connection(), configuration=pipeline(connection()))
    named.organization_id = 2
    with pytest.raises(HTTPException) as caught:
        await service.resolve_model_configuration(
            1, api_override={"model_configuration_uuid": named.uuid}
        )
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_common_dograh_key_is_reused_on_hydration(catalog):
    one = connection(keys=["first", "shared"])
    two = catalog(connection(keys=["shared", "other"]))
    config = pipeline(one)
    config["tts"] = selection(two)
    catalog(one, default=True, configuration=config)
    result = await service.resolve_model_configuration(1)
    assert {
        result.effective.llm.api_key,
        result.effective.stt.api_key,
        result.effective.tts.api_key,
    } == {"shared"}
    assert "shared" not in str(result.snapshot)
    one.is_active = False
    hydrated = await service.hydrate_model_configuration_snapshot(
        1, {**result.snapshot, "preparation_state": "ready"}
    )
    assert hydrated.llm.api_key == "shared"
    assert hydrated.tts.api_key == "shared"
    preview = service.public_snapshot(result.snapshot)
    assert "api_key_index" not in str(preview)


@pytest.mark.asyncio
async def test_hydration_uses_current_credentials_and_saved_model_settings(catalog):
    row = connection(keys="original")
    config = pipeline(row)
    config["llm"]["settings"] = {"temperature": 0.4}
    catalog(row, default=True, configuration=config)
    result = await service.resolve_model_configuration(1)
    row.credentials = {"api_key": "updated"}
    row.revision += 1
    hydrated = await service.hydrate_model_configuration_snapshot(1, result.snapshot)
    assert (
        hydrated.llm.api_key
        == hydrated.tts.api_key
        == hydrated.stt.api_key
        == "updated"
    )
    assert hydrated.llm.temperature == 0.4


@pytest.mark.asyncio
async def test_disjoint_dograh_keys_are_rejected(catalog):
    one = connection(keys=["a", "b"])
    two = catalog(connection(keys=["c", "d"]))
    config = pipeline(one)
    config["tts"] = selection(two)
    catalog(one, default=True, configuration=config)
    with pytest.raises(HTTPException, match="share a service key"):
        await service.resolve_model_configuration(1)


@pytest.mark.asyncio
async def test_inactive_pipeline_services_do_not_affect_realtime_authorization(catalog):
    dograh = catalog(connection(keys="unrelated"))
    llm = catalog(connection("openai"))
    realtime = catalog(connection("openai_realtime"))
    config = {
        "mode": "realtime",
        "llm": selection(llm),
        "realtime": selection(realtime),
        "tts": selection(dograh),
    }
    result = await service.resolve_inline_model_configuration(1, config)
    assert result.effective.managed_service_version is None
    assert result.effective.tts is None
    assert set(result.snapshot["services"]) == {"llm", "realtime"}


@pytest.mark.asyncio
async def test_realtime_requires_text_llm(catalog):
    realtime = catalog(connection("openai_realtime"))
    with pytest.raises(HTTPException):
        await service.resolve_inline_model_configuration(
            1, {"mode": "realtime", "realtime": selection(realtime)}
        )


@pytest.mark.asyncio
async def test_computed_tts_model_is_not_an_input_setting(catalog):
    dograh = connection()
    config = pipeline(dograh)
    deepgram = catalog(connection("deepgram"))
    config["tts"] = selection(deepgram, voice="aura-2-asteria-en")
    catalog(dograh, default=True, configuration=config)
    result = await service.resolve_model_configuration(1)
    assert result.effective.tts.model == "aura-2"
    assert "model" not in result.snapshot["services"]["tts"]["settings"]
    with pytest.raises(HTTPException):
        await service.resolve_model_configuration(
            1, api_override={"tts": {"settings": {"model": "aura-1"}}}
        )


def test_catalog_separates_credentials_connection_and_model_fields():
    entry = service.model_connection_catalog()["services"]["llm"]["google_vertex"]
    assert "credentials" in entry["credential_fields"]
    assert "project_id" in entry["connection_fields"]
    assert "fallback_model" in entry["settings_schema"]["properties"]
    assert "api_key" not in entry["settings_schema"]["properties"]


def test_connection_validation_is_local_and_rejects_unknown_credentials():
    service.validate_provider_connection("dograh", {"api_key": ["one", "two"]}, {})
    with pytest.raises(HTTPException):
        service.validate_provider_connection("dograh", {"password": "secret"}, {})
    with pytest.raises(HTTPException):
        service.validate_provider_connection("dograh", {"api_key": ""}, {})


@pytest.mark.asyncio
async def test_transfer_can_reuse_existing_authorized_dograh_key(catalog):
    catalog(connection(keys=["other", "authorized"]), default=True)
    result = await service.resolve_model_configuration(
        1, preferred_dograh_key="authorized"
    )
    assert result.effective.llm.api_key == "authorized"
    with pytest.raises(HTTPException):
        await service.resolve_model_configuration(1, preferred_dograh_key="unrelated")


@pytest.mark.asyncio
async def test_embedding_index_compatibility_checks_only_selected_documents(
    monkeypatch,
):
    from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
    from api.services.configuration.registry import OpenAIEmbeddingsConfiguration

    lookup = AsyncMock(
        return_value=[{"model": "text-embedding-3-small", "dimension": 1536}]
    )
    monkeypatch.setattr(
        service.db_client, "get_model_configuration_embedding_spaces", lookup
    )
    effective = EffectiveAIModelConfiguration(
        embeddings=OpenAIEmbeddingsConfiguration(api_key="secret")
    )
    await service.validate_embedding_compatibility(1, effective, ["selected-document"])
    lookup.assert_awaited_once_with(1, document_uuids=["selected-document"])
    lookup.reset_mock()
    await service.validate_embedding_compatibility(1, effective, [])
    lookup.assert_not_awaited()
    with pytest.raises(HTTPException, match="require an embedding"):
        await service.validate_embedding_compatibility(
            1, EffectiveAIModelConfiguration(), ["selected-document"]
        )
    lookup.return_value = [{"model": "older-model", "dimension": 1536}]
    with pytest.raises(HTTPException, match="does not match"):
        await service.validate_embedding_compatibility(1, effective)
    lookup.return_value = [{"model": "text-embedding-3-small", "dimension": 384}]
    with pytest.raises(HTTPException, match="does not match"):
        await service.validate_embedding_compatibility(1, effective)


@pytest.mark.asyncio
async def test_old_write_guard_does_not_resolve_or_probe_credentials(monkeypatch):
    monkeypatch.setattr(
        service.db_client,
        "get_configuration",
        AsyncMock(return_value=SimpleNamespace(value=str(uuid4()))),
    )
    with pytest.raises(HTTPException) as caught:
        await service.reject_legacy_model_configuration_write(1)
    assert caught.value.status_code == 409
    assert "/api/v1/model-connections" in caught.value.detail


@pytest.mark.asyncio
async def test_http_connection_responses_and_errors_never_return_credentials(
    monkeypatch,
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.model_connections import router
    from api.services.auth.depends import get_user_with_selected_organization

    row = connection(keys="private-secret")
    row.name = "Dograh"
    monkeypatch.setattr(
        service.db_client, "list_provider_connections", AsyncMock(return_value=[row])
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: (
        SimpleNamespace(selected_organization_id=1)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/model-connections/provider-connections")
        assert response.status_code == 200
        assert response.json()[0]["configured_credentials"] == ["api_key"]
        assert "private-secret" not in response.text
        response = await client.patch(
            f"/model-connections/provider-connections/{row.uuid}",
            json={"credentials": {"api_key": "private-secret"}, "name": None},
        )
        assert response.status_code == 422
        assert "private-secret" not in response.text


@pytest.mark.asyncio
async def test_legacy_routes_reject_v3_shadow_writes(monkeypatch):
    from api.routes import organization, user
    from api.schemas.ai_model_configuration import (
        EffectiveAIModelConfiguration,
        OrganizationAIModelConfigurationV2,
    )
    from api.services.configuration.ai_model_configuration import (
        ResolvedAIModelConfiguration,
    )

    monkeypatch.setattr(
        service.db_client,
        "get_configuration",
        AsyncMock(return_value=SimpleNamespace(value=str(uuid4()))),
    )
    resolved = ResolvedAIModelConfiguration(
        effective=EffectiveAIModelConfiguration(), source="organization_v3"
    )
    monkeypatch.setattr(
        user, "get_resolved_ai_model_configuration", AsyncMock(return_value=resolved)
    )
    caller = SimpleNamespace(selected_organization_id=1, id=1, provider_id="test")
    config = OrganizationAIModelConfigurationV2.model_validate(
        {"mode": "dograh", "dograh": {"api_key": "secret"}}
    )
    for action in (
        lambda: organization.save_model_configuration_v2(config, caller),
        lambda: organization.migrate_model_configuration_v2(force=True, user=caller),
        lambda: user.update_user_configurations(
            user.UserConfigurationRequestResponseSchema(llm={"temperature": 0.3}),
            caller,
        ),
    ):
        with pytest.raises(HTTPException) as caught:
            await action()
        assert caught.value.status_code == 409
    validator = AsyncMock()
    monkeypatch.setattr(user, "UserConfigurationValidator", validator)
    assert await user.validate_user_configurations(
        validity_ttl_seconds=0, user=caller
    ) == {"status": [{"model": "all", "message": "ok"}]}
    validator.assert_not_called()
