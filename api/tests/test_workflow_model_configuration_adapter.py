"""Legacy workflow forms remain editable after organization catalog bootstrap."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes import workflow as routes
from api.services.auth.depends import get_user
from api.services.configuration import model_connections, workflow_model_configuration
from api.services.configuration.masking import mask_workflow_configurations


@pytest.fixture
def adapter_route(monkeypatch):
    released = SimpleNamespace(
        id=10,
        workflow_json={"nodes": [], "edges": []},
        workflow_configurations={},
        template_context_variables={},
        version_number=1,
        status="published",
    )
    draft = SimpleNamespace(
        id=11,
        workflow_json={"nodes": [], "edges": []},
        workflow_configurations={},
        template_context_variables={},
        version_number=2,
        status="draft",
    )
    workflow = SimpleNamespace(
        id=1,
        name="Adapter fixture",
        status="active",
        created_at=datetime.now(UTC),
        current_definition_id=10,
        released_definition=released,
        call_disposition_codes={},
        workflow_configurations={},
    )
    default_uuid = str(uuid4())
    database = SimpleNamespace(
        get_configuration=AsyncMock(
            side_effect=lambda org, key: SimpleNamespace(
                value=default_uuid
                if key == "MODEL_CONFIGURATION_DEFAULT_UUID"
                else {
                    "version": 2,
                    "mode": "dograh",
                    "dograh": {"api_key": "org-private-key"},
                }
            )
        ),
        get_workflow=AsyncMock(return_value=workflow),
        get_draft_version=AsyncMock(return_value=draft),
        update_workflow=AsyncMock(),
    )

    async def update_workflow(**kwargs):
        draft.workflow_configurations = kwargs["workflow_configurations"]
        return workflow

    database.update_workflow.side_effect = update_workflow
    imported = {
        "version": 3,
        "mode": "pipeline",
        "llm": {
            "provider_connection_uuid": str(uuid4()),
            "settings": {"temperature": 0.4},
        },
    }
    imported_inputs = []

    async def import_legacy(organization_id, configuration):
        imported_inputs.append(deepcopy(configuration))
        return imported

    importer = AsyncMock(side_effect=import_legacy)
    resolver = AsyncMock()
    provider_validator = Mock()
    monkeypatch.setattr(routes, "db_client", database)
    monkeypatch.setattr(
        routes,
        "apply_external_pbx_mapping_policy",
        AsyncMock(side_effect=lambda incoming, **kwargs: incoming),
    )
    monkeypatch.setattr(
        routes, "validate_workflow_tool_name_collisions", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(routes, "UserConfigurationValidator", provider_validator)
    monkeypatch.setattr(
        workflow_model_configuration,
        "ensure_legacy_workflow_model_configuration",
        importer,
    )
    monkeypatch.setattr(model_connections, "resolve_model_configuration", resolver)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=1, provider_id="user-1", selected_organization_id=42
    )
    return SimpleNamespace(
        client=TestClient(app),
        database=database,
        draft=draft,
        importer=importer,
        imported_inputs=imported_inputs,
        resolver=resolver,
        provider_validator=provider_validator,
    )


@pytest.mark.parametrize(
    "legacy",
    [
        {
            "model_configuration_v2_override": {
                "version": 2,
                "mode": "dograh",
                "dograh": {"api_key": "workflow-private-key", "temperature": 0.4},
            }
        },
        {
            "model_overrides": {
                "llm": {
                    "provider": "google",
                    "api_key": "workflow-private-key",
                    "model": "gemini-2.5-flash",
                    "temperature": 0.4,
                }
            }
        },
    ],
)
def test_old_masked_form_saves_catalog_inline_semantically_without_provider_probe(
    adapter_route, legacy
):
    state = adapter_route
    state.draft.workflow_configurations = deepcopy(legacy)
    incoming = {**mask_workflow_configurations(legacy), "max_call_duration": 90}
    response = state.client.put(
        "/workflow/1", json={"workflow_configurations": incoming}
    )
    assert response.status_code == 200, response.text
    state.provider_validator.assert_not_called()
    state.resolver.assert_awaited_once()
    imported = state.imported_inputs[0]
    if "model_overrides" in imported:
        assert imported["model_overrides"]["llm"]["api_key"] == "workflow-private-key"
    else:
        assert (
            imported["model_configuration_v2_override"]["dograh"]["api_key"]
            == "workflow-private-key"
        )
    saved = state.database.update_workflow.await_args.kwargs["workflow_configurations"]
    assert saved["max_call_duration"] == 90
    assert "model_configuration_override" in saved
    assert "model_overrides" not in saved
    assert "model_configuration_v2_override" not in saved
    assert "workflow-private-key" not in response.text
    state.database.get_workflow.assert_awaited_once_with(1, organization_id=42)


def test_echoed_legacy_payload_preserves_existing_modern_selection(adapter_route):
    state = adapter_route
    legacy = {
        "model_configuration_v2_override": {
            "version": 2,
            "mode": "dograh",
            "dograh": {"api_key": "workflow-private-key"},
        }
    }
    modern = {
        "model_configuration_uuid": str(uuid4()),
        "llm": {"settings": {"temperature": 0.9}},
    }
    state.draft.workflow_configurations = {
        **legacy,
        "model_configuration_override": modern,
    }
    response = state.client.put(
        "/workflow/1",
        json={
            "workflow_configurations": {
                **mask_workflow_configurations(legacy),
                "max_call_duration": 90,
            }
        },
    )
    assert response.status_code == 200, response.text
    state.importer.assert_not_awaited()
    state.provider_validator.assert_not_called()
    assert (
        state.database.update_workflow.await_args.kwargs["workflow_configurations"][
            "model_configuration_override"
        ]
        == modern
    )


def test_legacy_adapter_checks_workflow_ownership_before_import(adapter_route):
    state = adapter_route
    state.database.get_workflow.return_value = None
    response = state.client.put(
        "/workflow/1",
        json={
            "workflow_configurations": {
                "model_overrides": {"llm": {"temperature": 0.4}}
            }
        },
    )
    assert response.status_code == 404
    state.importer.assert_not_awaited()
    state.database.get_draft_version.assert_not_awaited()
    state.database.update_workflow.assert_not_awaited()


def test_bad_modern_reference_is_rejected_before_workflow_write(adapter_route):
    state = adapter_route
    state.resolver.side_effect = HTTPException(
        status_code=404, detail="Provider connection not found"
    )
    response = state.client.put(
        "/workflow/1",
        json={
            "workflow_configurations": {
                "model_configuration_override": {
                    "llm": {"provider_connection_uuid": str(uuid4())}
                }
            }
        },
    )
    assert response.status_code == 404
    state.database.update_workflow.assert_not_awaited()
    state.provider_validator.assert_not_called()


def test_unmatched_mask_is_rejected_without_exposing_payload(adapter_route):
    state = adapter_route
    response = state.client.put(
        "/workflow/1",
        json={
            "workflow_configurations": {
                "model_configuration_v2_override": {
                    "version": 2,
                    "mode": "dograh",
                    "dograh": {"api_key": "******unmatched-private"},
                }
            }
        },
    )
    assert response.status_code == 422
    assert "unmatched-private" not in response.text
    state.importer.assert_not_awaited()
    state.database.update_workflow.assert_not_awaited()
    state.provider_validator.assert_not_called()


def test_removing_model_keys_does_not_resurrect_existing_override(adapter_route):
    state = adapter_route
    state.draft.workflow_configurations = {
        "model_configuration_override": {"model_configuration_uuid": str(uuid4())}
    }
    response = state.client.put(
        "/workflow/1", json={"workflow_configurations": {"max_call_duration": 90}}
    )
    assert response.status_code == 200, response.text
    assert (
        "model_configuration_override"
        not in state.database.update_workflow.await_args.kwargs[
            "workflow_configurations"
        ]
    )
    state.importer.assert_not_awaited()
