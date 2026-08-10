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


def _header_access(headers: dict[str, str]):
    """Return a reader plus the list of header names it was asked for."""
    requested: list[str] = []

    async def read_header(name: str) -> str:
        requested.append(name)
        return headers.get(name, "")

    return read_header, requested


@pytest.mark.asyncio
async def test_vicidial_adapter_captures_identity_and_configured_lead_fields():
    adapter = create_adapter(_vicidial_config())
    headers = {
        "X-VICIDIAL-callerid": "M123",
        "X-VICIDIAL-user": "remote-agent",
        "X-VICIDIAL-lead_id": "42",
        "X-VICIDIAL-campaign_id": "campaign",
        "X-VICIDIAL-ingroup_id": "source-group",
        "X-VICIDIAL-first_name": "Ada",
        "X-VICIDIAL-comments": "  prefers mornings  ",
        "X-VICIDIAL-address2": "",
    }
    read_header, requested = _header_access(headers)

    identity = await adapter.capture_call_identity(
        read_header, ["first_name", "comments", "address2"]
    )

    assert identity == {
        "type": "vicidial",
        "callerid": "M123",
        "agent_user": "remote-agent",
        "lead_id": "42",
        "campaign_id": "campaign",
        "ingroup_id": "source-group",
        "lead": {
            "callerid": "M123",
            "user": "remote-agent",
            "lead_id": "42",
            "campaign_id": "campaign",
            "ingroup_id": "source-group",
            "first_name": "Ada",
            "comments": "prefers mornings",
        },
    }
    # Exactly one read per configured field, and no enumeration request.
    assert requested == [
        "X-VICIDIAL-callerid",
        "X-VICIDIAL-user",
        "X-VICIDIAL-lead_id",
        "X-VICIDIAL-campaign_id",
        "X-VICIDIAL-ingroup_id",
        "X-VICIDIAL-first_name",
        "X-VICIDIAL-comments",
        "X-VICIDIAL-address2",
    ]


@pytest.mark.asyncio
async def test_vicidial_adapter_reads_only_identity_fields_when_unconfigured():
    adapter = create_adapter(_vicidial_config())
    headers = {
        "X-VICIDIAL-callerid": "M123",
        "X-VICIDIAL-user": "remote-agent",
        "X-VICIDIAL-lead_id": "42",
        "X-VICIDIAL-first_name": "Ada",
    }
    read_header, requested = _header_access(headers)

    identity = await adapter.capture_call_identity(read_header)

    assert identity == {
        "type": "vicidial",
        "callerid": "M123",
        "agent_user": "remote-agent",
        "lead_id": "42",
        "campaign_id": "",
        "ingroup_id": "",
        "lead": {
            "callerid": "M123",
            "user": "remote-agent",
            "lead_id": "42",
        },
    }
    # The unconfigured lead field is never fetched.
    assert "X-VICIDIAL-first_name" not in requested
    assert len(requested) == 5


@pytest.mark.asyncio
async def test_vicidial_adapter_ignores_duplicate_and_invalid_lead_fields():
    adapter = create_adapter(_vicidial_config())
    headers = {
        "X-VICIDIAL-callerid": "M123",
        "X-VICIDIAL-user": "remote-agent",
        "X-VICIDIAL-first_name": "Ada",
    }
    read_header, requested = _header_access(headers)

    await adapter.capture_call_identity(
        read_header,
        # duplicate, identity field, whitespace, empty, and injection-shaped
        ["first_name", " first_name ", "callerid", "", "bad name)"],
    )

    assert requested.count("X-VICIDIAL-first_name") == 1
    assert requested.count("X-VICIDIAL-callerid") == 1
    assert len(requested) == 6


@pytest.mark.asyncio
async def test_vicidial_adapter_returns_none_without_callerid():
    adapter = create_adapter(_vicidial_config())
    read_header, _ = _header_access({"X-VICIDIAL-first_name": "Ada"})

    identity = await adapter.capture_call_identity(read_header, ["first_name"])

    assert identity is None


def _ari_connection(monkeypatch, variables: dict[str, str]):
    """An ARIConnection whose ARI variable reads are recorded, not sent."""
    from api.services.telephony import ari_manager

    connection = ari_manager.ARIConnection(
        organization_id=7,
        telephony_configuration_id=1,
        ari_endpoint="http://asterisk.example.com",
        app_name="dograh",
        app_password="secret",
        external_pbx_config=_vicidial_config(),
    )
    requested: list[str] = []

    async def fake_get_channel_var(channel_id: str, variable: str) -> str:
        requested.append(variable)
        return variables.get(variable, "")

    monkeypatch.setattr(connection, "_get_channel_var", fake_get_channel_var)
    return connection, requested


@pytest.mark.asyncio
async def test_available_headers_are_listed_in_one_request(monkeypatch):
    from api.services.telephony import ari_manager

    monkeypatch.setattr(ari_manager, "LOG_EXTERNAL_PBX_AVAILABLE_HEADERS", True)
    connection, requested = _ari_connection(
        monkeypatch,
        {
            "PJSIP_HEADERS(X-VICIDIAL-)": (
                "X-VICIDIAL-callerid,X-VICIDIAL-user,X-VICIDIAL-first_name"
            ),
            "PJSIP_HEADER(read,X-VICIDIAL-callerid)": "M123",
            "PJSIP_HEADER(read,X-VICIDIAL-user)": "remote-agent",
        },
    )

    await connection._capture_external_pbx_call("chan-1", "PJSIP/inbound-0001")

    # Enumeration is a single request regardless of how many headers exist.
    assert requested.count("PJSIP_HEADERS(X-VICIDIAL-)") == 1
    # ...and it does not pull the values of the fields it merely lists.
    assert "PJSIP_HEADER(read,X-VICIDIAL-first_name)" not in requested


@pytest.mark.asyncio
async def test_available_header_listing_is_skipped_when_disabled(monkeypatch):
    from api.services.telephony import ari_manager

    monkeypatch.setattr(ari_manager, "LOG_EXTERNAL_PBX_AVAILABLE_HEADERS", False)
    connection, requested = _ari_connection(
        monkeypatch, {"PJSIP_HEADER(read,X-VICIDIAL-callerid)": "M123"}
    )

    await connection._capture_external_pbx_call("chan-1", "PJSIP/inbound-0001")

    assert not any(name.startswith("PJSIP_HEADERS(") for name in requested)
    assert len(requested) == 5


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
