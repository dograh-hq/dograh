from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
async def test_vicidial_lead_header_log_separates_empty_from_never_requested():
    """A blank header and an unconfigured one both vanish from ``lead``.

    They need opposite fixes — one is PBX/lead data, the other a workflow
    setting — so the log has to tell them apart.
    """
    adapter = create_adapter(_vicidial_config())
    headers = {
        "X-VICIDIAL-callerid": "M123",
        "X-VICIDIAL-first_name": "Ada",
        # state is configured but the PBX sends it blank
        "X-VICIDIAL-state": "",
    }
    read_header, _ = _header_access(headers)

    with (
        patch(
            "api.services.telephony.providers.ari.external_pbx.vicidial.logger.info"
        ) as log_info,
        patch(
            "api.services.telephony.providers.ari.external_pbx.vicidial.logger.warning"
        ) as log_warning,
    ):
        await adapter.capture_call_identity(
            read_header, ["first_name", "state", "bad name)"]
        )

    messages = " ".join(call.args[0] for call in log_info.call_args_list)
    assert "requested=['first_name', 'state']" in messages
    assert "populated=['first_name']" in messages
    assert "empty=['state']" in messages
    # Header values are customer PII; only names belong in the log.
    assert "Ada" not in messages and "M123" not in messages
    # An unusable configured name is a misconfiguration, not a PBX problem.
    assert "bad name)" in " ".join(call.args[0] for call in log_warning.call_args_list)


@pytest.mark.asyncio
async def test_vicidial_adapter_returns_none_without_callerid():
    adapter = create_adapter(_vicidial_config())
    read_header, _ = _header_access({"X-VICIDIAL-first_name": "Ada"})

    identity = await adapter.capture_call_identity(read_header, ["first_name"])

    assert identity is None


class _StubResponse:
    def __init__(self, status: int, body: str = ""):
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _StubSession:
    def __init__(self, response: _StubResponse):
        self._response = response
        self.requests: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.requests.append((url, kwargs))
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_vicidial_update_lead_requests_a_text_response():
    """non_agent_api.php returns an empty body unless format=text is sent.

    The update still applies, so the only symptom is a call that reports
    "VICIdial rejected the lead update" while the lead changes anyway.
    """
    adapter = create_adapter(_vicidial_config())
    session = _StubSession(
        _StubResponse(200, "SUCCESS: update_lead LEAD HAS BEEN UPDATED - u|42|1|||")
    )

    with patch(
        "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await adapter.update_fields({"lead_id": "42"}, {"comments": "hello"})

    assert result.ok
    url, kwargs = session.requests[0]
    assert url == "https://vici.example.com/vicidial/non_agent_api.php"
    assert kwargs["params"]["format"] == "text"
    assert kwargs["params"]["function"] == "update_lead"
    assert kwargs["params"]["lead_id"] == "42"
    assert kwargs["params"]["comments"] == "hello"


@pytest.mark.asyncio
async def test_vicidial_update_lead_treats_an_empty_body_as_a_rejection():
    adapter = create_adapter(_vicidial_config())
    session = _StubSession(_StubResponse(200, ""))

    with patch(
        "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await adapter.update_fields({"lead_id": "42"}, {"comments": "hello"})

    assert not result.ok
    assert result.message == "VICIdial rejected the lead update"


@pytest.mark.asyncio
async def test_vicidial_update_lead_log_redacts_rejection_body():
    adapter = create_adapter(_vicidial_config())
    response_body = (
        "ERROR: update_lead PERMISSION DENIED - "
        "lead-api-user|42|+14155550123|Ada Lovelace"
    )
    session = _StubSession(_StubResponse(200, response_body))

    with (
        patch(
            "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.ari.external_pbx.vicidial.logger.info"
        ) as log_info,
    ):
        result = await adapter.update_fields(
            {"lead_id": "42"}, {"comments": "private note"}
        )

    assert not result.ok
    messages = " ".join(call.args[0] for call in log_info.call_args_list)
    assert "response_code=error" in messages
    for sensitive_value in (
        response_body,
        "lead-api-user",
        "42",
        "+14155550123",
        "Ada Lovelace",
        "private note",
    ):
        assert sensitive_value not in messages


@pytest.mark.asyncio
async def test_vicidial_update_lead_asks_for_custom_fields():
    """Without custom_fields=Y, VICIdial ignores every custom-field parameter.

    No update, no error — just an empty body. Which destination fields a
    deployment defined as custom is not knowable here, so the flag always goes.
    """
    adapter = create_adapter(_vicidial_config())
    session = _StubSession(
        _StubResponse(200, "SUCCESS: update_lead LEAD HAS BEEN UPDATED - u|42|1|||")
    )

    with patch(
        "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
        return_value=session,
    ):
        await adapter.update_fields({"lead_id": "42"}, {"Medicaid": "yes"})

    _, kwargs = session.requests[0]
    assert kwargs["params"]["custom_fields"] == "Y"


@pytest.mark.asyncio
async def test_vicidial_update_lead_never_puts_a_mapped_field_first():
    """VICIdial's custom-field parser ignores the first query-string parameter.

    A mapped field placed there is dropped silently — no error, no update — so
    the control parameters must lead and the mapped fields follow.
    """
    adapter = create_adapter(_vicidial_config())
    session = _StubSession(
        _StubResponse(200, "SUCCESS: update_lead LEAD HAS BEEN UPDATED - u|42|1|||")
    )

    with patch(
        "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
        return_value=session,
    ):
        await adapter.update_fields(
            {"lead_id": "42"}, {"Medicaid": "yes", "MedSupp": "no"}
        )

    keys = list(session.requests[0][1]["params"])
    assert keys[0] not in ("Medicaid", "MedSupp")
    # Every mapped field must sit after every control parameter.
    assert min(keys.index("Medicaid"), keys.index("MedSupp")) > keys.index(
        "custom_fields"
    )


@pytest.mark.asyncio
async def test_vicidial_update_lead_rejects_fields_shadowing_control_params():
    """Mapped fields are appended last, so a collision would hijack the call."""
    adapter = create_adapter(_vicidial_config())
    session = _StubSession(
        _StubResponse(200, "SUCCESS: update_lead LEAD HAS BEEN UPDATED - u|42|1|||")
    )

    with patch(
        "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
        return_value=session,
    ):
        await adapter.update_fields(
            {"lead_id": "42"},
            {"format": "json", "custom_fields": "N", "function": "add_lead"},
        )

    assert not session.requests, "a request with only reserved names must not be sent"


@pytest.mark.asyncio
async def test_vicidial_update_lead_accepts_a_custom_fields_only_notice():
    """A custom-fields-only write never emits a SUCCESS line, only a NOTICE."""
    adapter = create_adapter(_vicidial_config())
    session = _StubSession(
        _StubResponse(
            200, "NOTICE: update_lead CUSTOM FIELDS VALUES UPDATED - |42|499|499|499|1"
        )
    )

    with patch(
        "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await adapter.update_fields({"lead_id": "42"}, {"Medicaid": "yes"})

    assert result.ok
    assert result.message == "VICIdial lead updated"


@pytest.mark.asyncio
async def test_vicidial_update_lead_accepts_mixed_standard_and_custom_response():
    """Standard and custom writes each announce themselves on their own line."""
    adapter = create_adapter(_vicidial_config())
    session = _StubSession(
        _StubResponse(
            200,
            "SUCCESS: update_lead LEAD HAS BEEN UPDATED - u|42|1|||\n"
            "NOTICE: update_lead CUSTOM FIELDS VALUES UPDATED - |42|499|499|499|1",
        )
    )

    with patch(
        "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await adapter.update_fields(
            {"lead_id": "42"}, {"comments": "note", "Medicaid": "yes"}
        )

    assert result.ok


@pytest.mark.asyncio
async def test_vicidial_update_lead_still_rejects_an_unrelated_notice():
    """Only the custom-field notice counts — not NOTICE lines in general."""
    adapter = create_adapter(_vicidial_config())
    session = _StubSession(
        _StubResponse(200, "NOTICE: update_lead NOTHING TO UPDATE - |42|")
    )

    with patch(
        "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await adapter.update_fields({"lead_id": "42"}, {"Medicaid": "yes"})

    assert not result.ok


@pytest.mark.asyncio
async def test_vicidial_update_lead_rejection_names_fields_and_reason():
    """A rejection has to say which fields were sent and why VICIdial refused.

    ``response_code=empty`` alone cannot separate "VICIdial does not know this
    field" from a missing lead or a revoked permission.
    """
    adapter = create_adapter(_vicidial_config())
    session = _StubSession(_StubResponse(200, ""))

    with (
        patch(
            "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.ari.external_pbx.vicidial.logger.warning"
        ) as log_warning,
    ):
        result = await adapter.update_fields(
            {"lead_id": "42"}, {"Medicaid": "yes", "MedSupp": "no"}
        )

    assert not result.ok
    messages = " ".join(call.args[0] for call in log_warning.call_args_list)
    assert "Medicaid" in messages and "MedSupp" in messages
    assert "<empty body>" in messages
    # Destination field names are workflow config; their values are lead data.
    assert "yes" not in messages and "no" not in messages


@pytest.mark.asyncio
async def test_vicidial_update_lead_rejection_reason_drops_echoed_parameters():
    adapter = create_adapter(_vicidial_config())
    # VICIdial appends the API user and search values after "|" or " - ".
    session = _StubSession(
        _StubResponse(
            200,
            "ERROR: update_lead NO MATCHES FOUND IN THE SYSTEM: |lead-api-user|42||",
        )
    )

    with (
        patch(
            "api.services.telephony.providers.ari.external_pbx.vicidial.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.ari.external_pbx.vicidial.logger.warning"
        ) as log_warning,
    ):
        await adapter.update_fields({"lead_id": "42"}, {"comments": "private note"})

    messages = " ".join(call.args[0] for call in log_warning.call_args_list)
    assert "NO MATCHES FOUND IN THE SYSTEM" in messages
    for echoed in ("lead-api-user", "private note"):
        assert echoed not in messages


@pytest.mark.asyncio
async def test_vicidial_update_lead_skips_are_not_silent():
    """Every early return ends the write; none may leave the log blank."""
    adapter = create_adapter(_vicidial_config())

    with patch(
        "api.services.telephony.providers.ari.external_pbx.vicidial.logger.info"
    ) as log_info:
        result = await adapter.update_fields({"lead_id": "42"}, {})
    assert result.ok
    assert "no field mappings resolved" in " ".join(
        call.args[0] for call in log_info.call_args_list
    )

    with patch(
        "api.services.telephony.providers.ari.external_pbx.vicidial.logger.warning"
    ) as log_warning:
        result = await adapter.update_fields({}, {"comments": "hello"})
    assert not result.ok
    assert "no lead_id was captured" in " ".join(
        call.args[0] for call in log_warning.call_args_list
    )


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
async def test_available_headers_are_always_listed_in_one_request(monkeypatch):
    from api.services.telephony import ari_manager

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

    with patch.object(ari_manager.logger, "info") as log_info:
        await connection._capture_external_pbx_call("chan-1", "PJSIP/inbound-0001")

    # Enumeration is a single request regardless of how many headers exist.
    assert requested.count("PJSIP_HEADERS(X-VICIDIAL-)") == 1
    # ...and it does not pull the values of the fields it merely lists.
    assert "PJSIP_HEADER(read,X-VICIDIAL-first_name)" not in requested
    assert any(
        "Available vicidial lead fields" in call.args[0]
        for call in log_info.call_args_list
    )


@pytest.mark.asyncio
async def test_captured_identity_log_contains_field_names_not_values(monkeypatch):
    from api.services.telephony import ari_manager

    connection, _ = _ari_connection(
        monkeypatch,
        {
            "PJSIP_HEADERS(X-VICIDIAL-)": (
                "X-VICIDIAL-callerid,X-VICIDIAL-user,X-VICIDIAL-lead_id,"
                "X-VICIDIAL-first_name"
            ),
            "PJSIP_HEADER(read,X-VICIDIAL-callerid)": "M123",
            "PJSIP_HEADER(read,X-VICIDIAL-user)": "remote-agent",
            "PJSIP_HEADER(read,X-VICIDIAL-lead_id)": "42",
            "PJSIP_HEADER(read,X-VICIDIAL-first_name)": "Ada",
        },
    )

    with patch.object(ari_manager.logger, "info") as log_info:
        await connection._capture_external_pbx_call(
            "chan-1", "PJSIP/inbound-0001", ["first_name"]
        )

    messages = " ".join(call.args[0] for call in log_info.call_args_list)
    assert "identity_fields" in messages
    assert "lead_fields" in messages
    for sensitive_value in ("M123", "remote-agent", "42", "Ada"):
        assert sensitive_value not in messages


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
async def test_context_mapping_resolves_ingroup_destination():
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
async def test_context_mapping_falls_through_to_later_rule():
    resolved = await transfer_resolver.resolve_transfer_config(
        tool=SimpleNamespace(tool_uuid="tool-1"),
        config={
            "destination_source": "context_mapping",
            "context_mapping": {
                "rules": [
                    {
                        "context_path": "qualified",
                        "routes": [{"context_value": "yes", "destination": "sales"}],
                    },
                    {
                        "context_path": "state",
                        "routes": [
                            {"context_value": "ca", "destination": "california"},
                            {"context_value": "tx", "destination": "texas"},
                        ],
                    },
                ],
                "fallback_destination": "source",
            },
        },
        arguments={},
        call_context_vars={},
        gathered_context_vars={
            "extracted_variables": {"qualified": "no", "state": " TX "}
        },
        organization_id=7,
        workflow_run_id=11,
    )

    assert resolved.destination == "texas"
    assert resolved.metadata["rule_index"] == 1
    assert resolved.metadata["context_path"] == "state"


@pytest.mark.asyncio
async def test_context_mapping_uses_fallback_after_all_rules_miss():
    resolved = await transfer_resolver.resolve_transfer_config(
        tool=SimpleNamespace(tool_uuid="tool-1"),
        config={
            "destination_source": "context_mapping",
            "context_mapping": {
                "rules": [
                    {
                        "context_path": "qualified",
                        "routes": [{"context_value": "yes", "destination": "sales"}],
                    },
                    {
                        "context_path": "state",
                        "routes": [
                            {"context_value": "ca", "destination": "california"}
                        ],
                    },
                ],
                "fallback_destination": "source",
            },
        },
        arguments={},
        call_context_vars={},
        gathered_context_vars={"extracted_variables": {"qualified": "no"}},
        organization_id=7,
        workflow_run_id=11,
    )

    assert resolved.destination == "source"
    assert resolved.metadata["fallback"] is True
    assert resolved.metadata["context_paths"] == ["qualified", "state"]


@pytest.mark.asyncio
async def test_context_mapping_raises_when_no_rule_matches():
    with pytest.raises(
        transfer_resolver.TransferResolutionError,
        match="No destination mapping matched",
    ):
        await transfer_resolver.resolve_transfer_config(
            tool=SimpleNamespace(tool_uuid="tool-1"),
            config={
                "destination_source": "context_mapping",
                "context_mapping": {
                    "rules": [
                        {
                            "context_path": "qualified",
                            "routes": [
                                {"context_value": "yes", "destination": "sales"}
                            ],
                        },
                        {
                            "context_path": "state",
                            "routes": [
                                {"context_value": "ca", "destination": "california"}
                            ],
                        },
                    ]
                },
            },
            arguments={},
            call_context_vars={},
            gathered_context_vars={"extracted_variables": {"state": "ny"}},
            organization_id=7,
            workflow_run_id=11,
        )


@pytest.mark.asyncio
async def test_context_mapping_is_available_without_external_pbx_configuration():
    resolved = await transfer_resolver.resolve_transfer_config(
        tool=SimpleNamespace(tool_uuid="tool-1"),
        config={
            "destination_source": "context_mapping",
            "context_mapping": {
                "context_path": "qualified",
                "routes": [
                    {"context_value": "yes", "destination": "+14155550123"},
                ],
            },
        },
        arguments={},
        call_context_vars={},
        gathered_context_vars={"qualified": "yes"},
        organization_id=None,
        workflow_run_id=11,
    )

    assert resolved.destination == "+14155550123"


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
