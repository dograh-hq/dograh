from unittest.mock import AsyncMock

import pytest

from api.routes import organization
from api.services import tool_management


def _credentials(password: str = "agent-secret") -> dict:
    return {
        "ari_endpoint": "https://asterisk.example.com",
        "app_name": "dograh",
        "app_password": "ari-secret",
        "external_pbx": {
            "type": "vicidial",
            "agent_api": {
                "url": "https://vici.example.com/agc/api.php",
                "username": "agent-user",
                "password": password,
            },
        },
    }


def test_nested_external_pbx_secrets_are_masked_without_mutating_source():
    credentials = _credentials()

    masked = organization._mask_sensitive("ari", credentials)

    assert masked["app_password"] != "ari-secret"
    assert masked["external_pbx"]["agent_api"]["password"] != "agent-secret"
    assert credentials["external_pbx"]["agent_api"]["password"] == "agent-secret"


def test_nested_masked_external_pbx_secrets_are_restored_on_update():
    existing = _credentials()
    request = organization._mask_sensitive("ari", existing)

    organization.preserve_masked_fields("ari", request, existing)

    assert request["app_password"] == "ari-secret"
    assert request["external_pbx"]["agent_api"]["password"] == "agent-secret"


@pytest.mark.asyncio
async def test_disabled_feature_preserves_existing_tool_mapping(monkeypatch):
    monkeypatch.setattr(
        tool_management,
        "external_pbx_integrations_enabled",
        AsyncMock(return_value=False),
    )
    definition = {
        "type": "transfer_call",
        "config": {
            "destination_source": "context_mapping",
            "context_mapping": {
                "context_path": "qualified",
                "routes": [{"context_value": "yes", "destination": "sales"}],
            },
        },
    }

    await tool_management.validate_external_pbx_tool_definition(
        definition,
        organization_id=7,
        existing_definition=definition,
    )

    changed = {
        "type": "transfer_call",
        "config": {"destination_source": "static", "destination": "+15555550100"},
    }
    with pytest.raises(tool_management.ToolManagementError) as exc_info:
        await tool_management.validate_external_pbx_tool_definition(
            changed,
            organization_id=7,
            existing_definition=definition,
        )

    assert exc_info.value.status_code == 403
