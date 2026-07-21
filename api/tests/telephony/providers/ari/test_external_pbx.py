from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as aioredis

from api.db import db_client
from api.services.telephony.external_pbx import resolve_external_pbx_field_mappings
from api.services.telephony.providers.ari.external_pbx import (
    ExternalPBXResult,
    create_adapter,
)
from api.services.telephony.providers.ari.strategies import ARIHangupStrategy
from api.services.workflow.tools import transfer_resolver


def _vicidial_config() -> dict:
    return {
        "type": "vicidial",
        "agent_api": {
            "url": "https://vici.example.com/agc/api.php",
            "username": "agent-api-user",
            "password": "secret",
            "source": "dograh",
        },
        "non_agent_api": {
            "url": "https://vici.example.com/vicidial/non_agent_api.php",
            "username": "lead-api-user",
            "password": "secret",
            "source": "dograh",
        },
    }


@pytest.mark.asyncio
async def test_vicidial_adapter_captures_call_identity_from_headers():
    adapter = create_adapter(_vicidial_config())
    headers = {
        "X-VICIDIAL-callerid": "M123",
        "X-VICIDIAL-user": "remote-agent",
        "X-VICIDIAL-lead_id": "42",
        "X-VICIDIAL-campaign_id": "campaign",
        "X-VICIDIAL-ingroup_id": "source-group",
    }

    async def read_header(name: str) -> str:
        return headers.get(name, "")

    identity = await adapter.capture_call_identity(read_header)

    assert identity == {
        "type": "vicidial",
        "callerid": "M123",
        "agent_user": "remote-agent",
        "lead_id": "42",
        "campaign_id": "campaign",
        "ingroup_id": "source-group",
    }


@pytest.mark.asyncio
async def test_vicidial_adapter_resolves_source_ingroup(monkeypatch):
    adapter = create_adapter(_vicidial_config())
    call_control = AsyncMock(
        return_value=ExternalPBXResult(True, "ingrouptransfer", "ok")
    )
    monkeypatch.setattr(adapter, "_agent_call_control", call_control)

    result = await adapter.transfer(
        {"callerid": "M123", "agent_user": "agent", "ingroup_id": "support"},
        "source",
    )

    assert result.ok is True
    call_control.assert_awaited_once_with(
        {"callerid": "M123", "agent_user": "agent", "ingroup_id": "support"},
        "INGROUPTRANSFER",
        ingroup_choices="support",
    )


def test_field_mapping_reads_extracted_variables_and_skips_empty_values():
    fields = resolve_external_pbx_field_mappings(
        {
            "extracted_variables": {"qualified": "yes", "empty": "  "},
            "call_disposition": "completed",
        },
        [
            {"context_path": "qualified", "destination_field": "address3"},
            {"context_path": "empty", "destination_field": "comments"},
            {
                "context_path": "call_disposition",
                "destination_field": "status_notes",
            },
        ],
    )

    assert fields == {"address3": "yes", "status_notes": "completed"}


@pytest.mark.asyncio
async def test_context_mapping_resolves_ingroup_destination(monkeypatch):
    monkeypatch.setattr(
        transfer_resolver,
        "external_pbx_integrations_enabled",
        AsyncMock(return_value=True),
    )

    resolved = await transfer_resolver.resolve_transfer_config(
        tool=SimpleNamespace(tool_uuid="tool-1"),
        config={
            "destination_source": "context_mapping",
            "context_mapping": {
                "context_path": "qualified",
                "routes": [
                    {"context_value": "YES", "destination": "sales"},
                ],
            },
        },
        arguments={},
        call_context_vars={},
        gathered_context_vars={"extracted_variables": {"qualified": " yes "}},
        organization_id=7,
        workflow_run_id=11,
    )

    assert resolved.destination == "sales"
    assert resolved.source == "context_mapping"


@pytest.mark.asyncio
async def test_context_mapping_is_disabled_at_runtime(monkeypatch):
    monkeypatch.setattr(
        transfer_resolver,
        "external_pbx_integrations_enabled",
        AsyncMock(return_value=False),
    )

    with pytest.raises(
        transfer_resolver.TransferResolutionError,
        match="External PBX integrations are disabled",
    ):
        await transfer_resolver.resolve_transfer_config(
            tool=SimpleNamespace(tool_uuid="tool-1"),
            config={
                "destination_source": "context_mapping",
                "context_mapping": {
                    "context_path": "qualified",
                    "routes": [
                        {"context_value": "yes", "destination": "sales"},
                    ],
                },
            },
            arguments={},
            call_context_vars={},
            gathered_context_vars={"qualified": "yes"},
            organization_id=7,
            workflow_run_id=11,
        )


@pytest.mark.asyncio
async def test_hangup_strategy_updates_lead_before_customer_leg(monkeypatch):
    redis = AsyncMock()
    redis.get.return_value = "11"
    monkeypatch.setattr(aioredis, "from_url", lambda *args, **kwargs: redis)
    run = SimpleNamespace(
        initial_context={
            "external_pbx_call": {
                "type": "vicidial",
                "callerid": "M123",
                "agent_user": "agent",
                "lead_id": "42",
            }
        },
        gathered_context={"extracted_variables": {"qualified": "yes"}},
        workflow=SimpleNamespace(organization_id=7),
    )
    monkeypatch.setattr(
        db_client, "get_workflow_run_by_id", AsyncMock(return_value=run)
    )
    monkeypatch.setattr(
        db_client,
        "get_workflow_run_configurations",
        AsyncMock(
            return_value={
                "external_pbx_field_mappings": [
                    {"context_path": "qualified", "destination_field": "address3"}
                ]
            }
        ),
    )
    adapter = SimpleNamespace(
        type="vicidial",
        update_fields=AsyncMock(
            return_value=ExternalPBXResult(True, "update_lead", "ok")
        ),
        hangup=AsyncMock(return_value=ExternalPBXResult(True, "hangup", "ok")),
    )

    await ARIHangupStrategy(adapter)._terminate_external_pbx_if_any("channel-1")

    adapter.update_fields.assert_awaited_once_with(
        run.initial_context["external_pbx_call"], {"address3": "yes"}
    )
    adapter.hangup.assert_awaited_once_with(run.initial_context["external_pbx_call"])
    redis.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_hangup_strategy_closes_redis_when_channel_has_no_run(monkeypatch):
    redis = AsyncMock()
    redis.get.return_value = None
    monkeypatch.setattr(aioredis, "from_url", lambda *args, **kwargs: redis)

    await ARIHangupStrategy()._terminate_external_pbx_if_any("missing-channel")

    redis.aclose.assert_awaited_once_with()
