from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.workflow import configuration_policy


def _stored_workflow(mappings: list[dict], lead_headers: list[str] | None = None):
    return SimpleNamespace(
        released_definition=SimpleNamespace(
            workflow_configurations={
                "external_pbx_field_mappings": mappings,
                "external_pbx_lead_headers": lead_headers or [],
            }
        )
    )


@pytest.mark.asyncio
async def test_disabled_external_pbx_policy_preserves_hidden_mappings(monkeypatch):
    mappings = [{"context_path": "qualified", "destination_field": "address3"}]
    get_workflow = AsyncMock(return_value=_stored_workflow(mappings))
    get_draft = AsyncMock(return_value=None)
    monkeypatch.setattr(
        configuration_policy,
        "external_pbx_integrations_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(configuration_policy.db_client, "get_workflow", get_workflow)
    monkeypatch.setattr(configuration_policy.db_client, "get_draft_version", get_draft)

    incoming = {"max_call_duration": 600}
    prepared = await configuration_policy.apply_external_pbx_mapping_policy(
        incoming,
        workflow_id=12,
        organization_id=7,
    )

    assert prepared == {
        "max_call_duration": 600,
        "external_pbx_field_mappings": mappings,
    }
    assert "external_pbx_field_mappings" not in incoming
    get_workflow.assert_awaited_once_with(12, organization_id=7)
    get_draft.assert_awaited_once_with(12)


@pytest.mark.asyncio
async def test_disabled_external_pbx_policy_rejects_mapping_changes(monkeypatch):
    stored = [{"context_path": "qualified", "destination_field": "address3"}]
    monkeypatch.setattr(
        configuration_policy,
        "external_pbx_integrations_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        configuration_policy.db_client,
        "get_workflow",
        AsyncMock(return_value=_stored_workflow(stored)),
    )
    monkeypatch.setattr(
        configuration_policy.db_client,
        "get_draft_version",
        AsyncMock(return_value=None),
    )

    with pytest.raises(configuration_policy.ExternalPBXConfigurationDisabledError):
        await configuration_policy.apply_external_pbx_mapping_policy(
            {
                "external_pbx_field_mappings": [
                    {"context_path": "qualified", "destination_field": "comments"}
                ]
            },
            workflow_id=12,
            organization_id=7,
        )


@pytest.mark.asyncio
async def test_disabled_external_pbx_policy_preserves_hidden_lead_headers(monkeypatch):
    mappings = [{"context_path": "qualified", "destination_field": "address3"}]
    lead_headers = ["first_name", "address1"]
    monkeypatch.setattr(
        configuration_policy,
        "external_pbx_integrations_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        configuration_policy.db_client,
        "get_workflow",
        AsyncMock(return_value=_stored_workflow(mappings, lead_headers)),
    )
    monkeypatch.setattr(
        configuration_policy.db_client,
        "get_draft_version",
        AsyncMock(return_value=None),
    )

    prepared = await configuration_policy.apply_external_pbx_mapping_policy(
        {"max_call_duration": 600},
        workflow_id=12,
        organization_id=7,
    )

    assert prepared == {
        "max_call_duration": 600,
        "external_pbx_field_mappings": mappings,
        "external_pbx_lead_headers": lead_headers,
    }


@pytest.mark.asyncio
async def test_disabled_external_pbx_policy_rejects_lead_header_changes(monkeypatch):
    monkeypatch.setattr(
        configuration_policy,
        "external_pbx_integrations_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        configuration_policy.db_client,
        "get_workflow",
        AsyncMock(return_value=_stored_workflow([], ["first_name"])),
    )
    monkeypatch.setattr(
        configuration_policy.db_client,
        "get_draft_version",
        AsyncMock(return_value=None),
    )

    with pytest.raises(configuration_policy.ExternalPBXConfigurationDisabledError):
        await configuration_policy.apply_external_pbx_mapping_policy(
            {"external_pbx_lead_headers": ["first_name", "address1"]},
            workflow_id=12,
            organization_id=7,
        )


@pytest.mark.asyncio
async def test_enabled_external_pbx_policy_does_not_load_workflow(monkeypatch):
    get_workflow = AsyncMock()
    monkeypatch.setattr(
        configuration_policy,
        "external_pbx_integrations_enabled",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(configuration_policy.db_client, "get_workflow", get_workflow)
    incoming = {"external_pbx_field_mappings": []}

    prepared = await configuration_policy.apply_external_pbx_mapping_policy(
        incoming,
        workflow_id=12,
        organization_id=7,
    )

    assert prepared is incoming
    get_workflow.assert_not_awaited()
