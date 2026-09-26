"""Unit tests for the VoiceLink telephony provider.

The VoiceLink SDK client is mocked throughout; nothing here reaches VoiceLink.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qsl, urlsplit

import pytest
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from voicelink.errors import VoiceLinkAPIError
from voicelink.models import Did, LeadResult, Page

from api.enums import TelephonyCallStatus
from api.schemas.telephony_config import TelephonyConfigRequest
from api.services.telephony import registry
from api.services.telephony.providers.voicelink import provider as provider_module
from api.services.telephony.providers.voicelink.config import (
    PRODUCTION_API_BASE_URL,
    UAT_API_BASE_URL,
    VoiceLinkConfigurationRequest,
)
from api.services.telephony.providers.voicelink.provider import VoiceLinkProvider

DID = "+910000000000"
CUSTOMER = "+910000000001"
RUN_PIPELINE = "api.services.pipecat.run_pipeline.run_pipeline_telephony"

START_FRAME = json.dumps(
    {
        "event": "start",
        "stream_sid": "stream-1",
        "start": {
            "stream_sid": "stream-1",
            "call_sid": "call-1",
            "account_sid": "123",
            "from": "0000000001",
            "to": "910000000000",
            "custom_parameters": {"campaign": "test"},
            "media_format": {"encoding": "audio/alaw", "sample_rate": "8000"},
        },
    }
)

# Shape of a Call Event webhook captured from the live platform (numbers faked).
COMPLETED_WEBHOOK = {
    "event": "call.completed",
    "timestamp": "2026-07-23T01:56:39.000+05:30",
    "call": {
        "id": "0de3a0e3-5f8e-4f33-ba9f-f53e1a5711a3",
        "direction": "outbound",
        "from": "910000000000",
        "to": "910000000001",
        "status": "failed",
        "durationSec": None,
        "customParameters": {"outboundQueueId": 300735},
    },
}


def _provider(**overrides) -> VoiceLinkProvider:
    config = {
        "api_token": "token-123",
        "client_id": 123,
        "api_base_url": UAT_API_BASE_URL,
        "from_numbers": [DID],
    }
    config.update(overrides)
    return VoiceLinkProvider(config)


def _mock_sdk_client() -> tuple[MagicMock, MagicMock]:
    """A patched ``VoiceLinkClient`` class and the client it yields."""
    client = MagicMock()
    client_cls = MagicMock()
    client_cls.return_value.__enter__.return_value = client
    client_cls.return_value.__exit__.return_value = None
    return client_cls, client


def _websocket(*messages: str) -> MagicMock:
    ws = MagicMock()
    ws.receive_text = AsyncMock(side_effect=list(messages))
    ws.close = AsyncMock()
    return ws


# ----------------------------------------------------------------- config


def test_config_defaults_to_production():
    config = VoiceLinkConfigurationRequest(api_token="t", client_id="123")
    assert config.provider == "voicelink"
    assert config.api_base_url == PRODUCTION_API_BASE_URL


def test_config_accepts_known_urls_with_trailing_slash_or_spaces():
    assert (
        VoiceLinkConfigurationRequest(
            api_token="t", client_id="123", api_base_url=f"{PRODUCTION_API_BASE_URL}/"
        ).api_base_url
        == PRODUCTION_API_BASE_URL
    )
    assert (
        VoiceLinkConfigurationRequest(
            api_token="t", client_id="123", api_base_url=f"  {UAT_API_BASE_URL}  "
        ).api_base_url
        == UAT_API_BASE_URL
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com",
        # Plain http would send the API token unencrypted.
        "http://app.voicelink.co.in/api",
    ],
)
def test_config_rejects_unknown_urls(url):
    with pytest.raises(ValidationError):
        VoiceLinkConfigurationRequest(api_token="t", client_id="123", api_base_url=url)


@pytest.mark.parametrize("raw", [123, "123", " 123 "])
def test_config_stores_client_id_as_text(raw):
    # Dograh matches inbound streams with ``credentials->>'client_id'`` (text);
    # a stored integer makes that Postgres comparison fail outright.
    assert (
        VoiceLinkConfigurationRequest(api_token="t", client_id=raw).client_id == "123"
    )


# "²" and Arabic-Indic digits pass ``str.isdigit()`` but are not ASCII numbers.
@pytest.mark.parametrize("raw", ["abc", "²", "١٢٣", "", "   ", None, "-1", "1.5"])
def test_config_rejects_invalid_client_id(raw):
    with pytest.raises(ValidationError):
        VoiceLinkConfigurationRequest(api_token="t", client_id=raw)


def test_config_requires_client_id():
    # Inbound streams are matched by client id, so a config without one could
    # attach numbers that never receive a call.
    with pytest.raises(ValidationError):
        VoiceLinkConfigurationRequest(api_token="t")


@pytest.mark.parametrize("token", ["", "   "])
def test_config_rejects_blank_api_token(token):
    with pytest.raises(ValidationError):
        VoiceLinkConfigurationRequest(api_token=token, client_id="123")


def test_provider_converts_client_id_for_sdk():
    assert _provider(client_id="560").client_id == 560
    assert _provider(client_id=None).client_id is None
    # The agent-stream route builds the provider with an empty config.
    assert VoiceLinkProvider({}).client_id is None


def test_config_union_dispatches_voicelink_payload():
    parsed = TypeAdapter(TelephonyConfigRequest).validate_python(
        {"provider": "voicelink", "api_token": "t", "client_id": 123}
    )
    assert isinstance(parsed, VoiceLinkConfigurationRequest)


def test_provider_is_registered():
    spec = registry.get("voicelink")
    assert spec.provider_cls is VoiceLinkProvider
    assert spec.transport_sample_rate == 8000
    assert spec.account_id_credential_field == "client_id"
    assert spec.config_loader({"api_token": "t"})["api_base_url"] == (
        PRODUCTION_API_BASE_URL
    )


# ---------------------------------------------------------------- helpers


def test_validate_config_requires_token():
    assert _provider().validate_config() is True
    assert _provider(api_token=None).validate_config() is False


@pytest.mark.parametrize(
    "number", ["+910000000001", "910000000001", "0000000001", "+91 00000-00001"]
)
def test_split_indian_number(number):
    assert VoiceLinkProvider._split_indian_number(number) == ("0000000001", "91")


def test_split_indian_number_rejects_other_countries():
    with pytest.raises(ValueError, match=r"\+91"):
        VoiceLinkProvider._split_indian_number("+14155550100")


# ---------------------------------------------------------------- outbound


@pytest.mark.asyncio
async def test_initiate_call_queues_lead_with_media_url():
    client_cls, client = _mock_sdk_client()
    client.calls.create.return_value = LeadResult.from_dict(
        {"outbound_queue_id": 300735}
    )

    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        patch.object(
            provider_module,
            "get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.example.test", "wss://api.example.test"),
        ),
    ):
        result = await _provider().initiate_call(
            to_number=CUSTOMER,
            webhook_url="https://unused.example.test",
            workflow_run_id=42,
            workflow_id=7,
            organization_id=9,
        )

    assert result.call_id == "300735"
    assert result.caller_number == DID
    assert result.provider_metadata == {"call_id": "300735"}

    client_cls.assert_called_once_with(
        "token-123", base_url=UAT_API_BASE_URL, client_id=123
    )
    kwargs = client.calls.create.call_args.kwargs
    assert kwargs["did_number"] == "910000000000"
    # National number + separate country code; the combined form fails with
    # cause 38 on VoiceLink.
    assert kwargs["customer_number"] == "0000000001"
    assert kwargs["country_code"] == "91"
    assert kwargs["websocket_url"].startswith(
        "wss://api.example.test/api/v1/telephony/ws/7/9/42"
    )
    assert kwargs["custom_parameters"] == {"workflow_run_id": 42}
    # Signed per-run status URL, so pre-media failures still end the run.
    assert kwargs["webhook_url"] == (
        "https://api.example.test/api/v1/telephony/voicelink/status-callback/42"
        f"?voicelink_auth={_provider()._status_callback_token(42)}"
    )


@pytest.mark.asyncio
async def test_initiate_call_surfaces_api_error_status():
    client_cls, client = _mock_sdk_client()
    client.calls.create.side_effect = VoiceLinkAPIError(
        "Insufficient balance", status_code=402
    )

    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        patch.object(
            provider_module,
            "get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.example.test", "wss://api.example.test"),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _provider().initiate_call(
            to_number=CUSTOMER,
            webhook_url="",
            workflow_run_id=42,
            workflow_id=7,
            organization_id=9,
        )

    assert exc_info.value.status_code == 402
    assert "Insufficient balance" in exc_info.value.detail


@pytest.mark.asyncio
async def test_initiate_call_requires_outbound_queue_id():
    client_cls, client = _mock_sdk_client()
    client.calls.create.return_value = LeadResult.from_dict({})

    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        patch.object(
            provider_module,
            "get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.example.test", "wss://api.example.test"),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _provider().initiate_call(
            to_number=CUSTOMER,
            webhook_url="",
            workflow_run_id=42,
            workflow_id=7,
            organization_id=9,
        )

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_initiate_call_without_caller_id_fails_before_dialling():
    client_cls, client = _mock_sdk_client()
    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        pytest.raises(ValueError, match="No phone numbers"),
    ):
        await _provider(from_numbers=[]).initiate_call(
            to_number=CUSTOMER,
            webhook_url="",
            workflow_run_id=42,
            workflow_id=7,
            organization_id=9,
        )
    client.calls.create.assert_not_called()


# ----------------------------------------------------------- phone numbers


@pytest.mark.asyncio
async def test_validate_phone_number_owned():
    client_cls, client = _mock_sdk_client()
    client.dids.list.return_value = Page(items=[Did(id=1, did_number="910000000000")])
    with patch.object(provider_module, "VoiceLinkClient", client_cls):
        result = await _provider().validate_phone_number(DID)
    assert result.ok is True
    assert client.dids.list.call_args.kwargs["search"] == "910000000000"


@pytest.mark.asyncio
async def test_validate_phone_number_not_owned():
    client_cls, client = _mock_sdk_client()
    client.dids.list.return_value = Page(items=[])
    with patch.object(provider_module, "VoiceLinkClient", client_cls):
        result = await _provider().validate_phone_number(DID)
    assert result.ok is False


# ---------------------------------------------------------- status webhook


def test_parse_status_callback_uses_outbound_queue_id():
    parsed = _provider().parse_status_callback(COMPLETED_WEBHOOK)
    # Matches the call_id initiate_call recorded, not the webhook's UUID.
    assert parsed["call_id"] == "300735"
    assert parsed["status"] is TelephonyCallStatus.FAILED
    assert parsed["direction"] == "outbound"
    assert parsed["duration"] is None
    assert parsed["extra"] is COMPLETED_WEBHOOK


def _webhook(status: str, call_status=None, duration=None) -> dict:
    call = {**COMPLETED_WEBHOOK["call"], "status": status, "durationSec": duration}
    if call_status is not None:
        call["callStatus"] = call_status
    return {**COMPLETED_WEBHOOK, "call": call}


@pytest.mark.parametrize(
    "status, call_status, expected",
    [
        # Live platform: coarse status plus the real reason in callStatus.
        ("failed", "NO ANSWER", TelephonyCallStatus.NO_ANSWER),
        ("failed", "BUSY", TelephonyCallStatus.BUSY),
        ("failed", "Canceled", TelephonyCallStatus.CANCELED),
        # A generic callStatus does not override the status.
        ("completed", "ANSWERED", TelephonyCallStatus.COMPLETED),
        ("failed", "SOMETHING ELSE", TelephonyCallStatus.FAILED),
        # Unrecognised status falls back to a recognised callStatus.
        ("weird", "COMPLETED", TelephonyCallStatus.COMPLETED),
    ],
)
def test_parse_status_callback_prefers_specific_call_status(
    status, call_status, expected
):
    parsed = _provider().parse_status_callback(_webhook(status, call_status))
    assert parsed["status"] is expected


def test_parse_status_callback_reports_duration_as_text():
    # StatusCallbackRequest.duration is Optional[str].
    parsed = _provider().parse_status_callback(_webhook("completed", duration=17))
    assert parsed["duration"] == "17"


STATUS_PATH = "https://api.example.test/api/v1/telephony/voicelink/status-callback"


@pytest.mark.asyncio
async def test_verify_inbound_signature_accepts_minted_url():
    provider = _provider()
    url = provider.build_status_callback_url("https://api.example.test/", 42)
    assert url.startswith(f"{STATUS_PATH}/42?voicelink_auth=")
    assert await provider.verify_inbound_signature(url, {}, {}) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        f"{STATUS_PATH}/42",
        f"{STATUS_PATH}/42?voicelink_auth=",
        f"{STATUS_PATH}/42?voicelink_auth={'0' * 64}",
        f"{STATUS_PATH}/42?voicelink_auth=%C3%A9",
        # Token minted for another run.
        f"{STATUS_PATH}/42?voicelink_auth={_provider()._status_callback_token(43)}",
        # Right token, wrong path.
        (
            "https://api.example.test/other/42?voicelink_auth="
            f"{_provider()._status_callback_token(42)}"
        ),
    ],
)
async def test_verify_inbound_signature_rejects_bad_tokens(url):
    assert await _provider().verify_inbound_signature(url, {}, {}) is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_url_from_another_config():
    for other in (_provider(api_token="other-token"), _provider(client_id=999)):
        url = other.build_status_callback_url("https://api.example.test", 42)
        assert await _provider().verify_inbound_signature(url, {}, {}) is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_fails_closed_without_token():
    provider = _provider()
    url = provider.build_status_callback_url("https://api.example.test", 42)
    assert await _provider(api_token=None).verify_inbound_signature(url, {}, {}) is (
        False
    )


# --------------------------------------------------------- media handshake


@pytest.mark.asyncio
async def test_handle_websocket_starts_pipeline_with_start_frame():
    ws = _websocket(START_FRAME)
    with patch(RUN_PIPELINE, new_callable=AsyncMock) as run_pipeline:
        await _provider().handle_websocket(
            ws, workflow_id=7, organization_id=9, workflow_run_id=42
        )

    ws.close.assert_not_called()
    kwargs = run_pipeline.call_args.kwargs
    assert kwargs["provider_name"] == "voicelink"
    assert kwargs["call_id"] == "call-1"
    # The transport replays the start frame into the serializer.
    assert kwargs["transport_kwargs"] == {"start_message": START_FRAME}


@pytest.mark.asyncio
async def test_handle_websocket_rejects_media_before_start():
    ws = _websocket(json.dumps({"event": "media", "media": {"payload": ""}}))
    with patch(RUN_PIPELINE, new_callable=AsyncMock) as run_pipeline:
        await _provider().handle_websocket(
            ws, workflow_id=7, organization_id=9, workflow_run_id=42
        )

    run_pipeline.assert_not_called()
    assert ws.close.call_args.kwargs["code"] == 4400


@pytest.mark.asyncio
async def test_handle_websocket_times_out_silent_socket():
    ws = MagicMock()

    async def never():
        await asyncio.sleep(10)

    ws.receive_text = never
    ws.close = AsyncMock()
    with (
        patch.object(provider_module, "HANDSHAKE_TIMEOUT_S", 0.01),
        patch(RUN_PIPELINE, new_callable=AsyncMock) as run_pipeline,
    ):
        await _provider().handle_websocket(
            ws, workflow_id=7, organization_id=9, workflow_run_id=42
        )

    run_pipeline.assert_not_called()
    assert ws.close.call_args.kwargs["code"] == 4408


# ------------------------------------------------------ inbound agent-stream


def _config_row(config_id: int, client_id, api_token: str = "t"):
    credentials = {"provider": "voicelink", "api_token": api_token}
    if client_id is not None:
        # Stored as text, as config.py saves it.
        credentials["client_id"] = str(client_id)
    return SimpleNamespace(id=config_id, credentials=credentials)


def _stream_params(api_token="t", client_id=123, workflow_id=7) -> dict:
    token = VoiceLinkProvider._stream_token(api_token, client_id, workflow_id)
    return {"vl_token": token}


async def _run_external(ws, db, params) -> AsyncMock:
    with (
        patch.object(provider_module, "db_client", db),
        patch(RUN_PIPELINE, new_callable=AsyncMock) as run_pipeline,
    ):
        await VoiceLinkProvider({}).handle_external_websocket(
            ws,
            organization_id=9,
            workflow_id=7,
            workflow_run_id=42,
            params=params,
        )
    return run_pipeline


def _db_with_configs(*rows) -> MagicMock:
    db = MagicMock()
    db.list_telephony_configurations_by_provider = AsyncMock(return_value=list(rows))
    db.update_workflow_run = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_external_websocket_matches_config_by_client_id():
    ws = _websocket(START_FRAME)
    db = _db_with_configs(_config_row(1, 999), _config_row(2, 123))

    run_pipeline = await _run_external(ws, db, _stream_params())

    db.list_telephony_configurations_by_provider.assert_awaited_once_with(
        9, "voicelink"
    )
    context = db.update_workflow_run.call_args.kwargs["initial_context"]
    # The transport loads credentials from this configuration.
    assert context["telephony_configuration_id"] == 2
    assert context["direction"] == "inbound"
    assert context["called_number"] == "910000000000"
    assert context["campaign"] == "test"
    assert run_pipeline.call_args.kwargs["call_id"] == "call-1"


@pytest.mark.asyncio
async def test_external_websocket_rejects_unknown_account():
    ws = _websocket(START_FRAME)
    db = _db_with_configs(_config_row(1, 999))

    run_pipeline = await _run_external(ws, db, _stream_params())

    run_pipeline.assert_not_called()
    db.update_workflow_run.assert_not_called()
    assert ws.close.call_args.kwargs["code"] == 4400


@pytest.mark.asyncio
async def test_external_websocket_rejects_config_without_client_id():
    # No sole-config fallback: account_sid is caller-supplied.
    ws = _websocket(START_FRAME)
    db = _db_with_configs(_config_row(5, None))

    run_pipeline = await _run_external(ws, db, _stream_params())

    run_pipeline.assert_not_called()
    db.update_workflow_run.assert_not_called()
    assert ws.close.call_args.kwargs["code"] == 4400


@pytest.mark.asyncio
async def test_external_websocket_rejects_start_without_account_sid():
    frame = json.loads(START_FRAME)
    del frame["start"]["account_sid"]
    ws = _websocket(json.dumps(frame))
    db = _db_with_configs(_config_row(2, 123))

    run_pipeline = await _run_external(ws, db, _stream_params())

    run_pipeline.assert_not_called()
    db.update_workflow_run.assert_not_called()
    assert ws.close.call_args.kwargs["code"] == 4400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"vl_token": ""},
        {"vl_token": "0" * 64},
        {"vl_token": "é"},
        # Signed with another configuration's API token.
        _stream_params(api_token="other-token"),
        # Signed for another workflow.
        _stream_params(workflow_id=8),
        # Signed for another account.
        _stream_params(client_id=999),
    ],
)
async def test_external_websocket_rejects_invalid_stream_token(params):
    ws = _websocket(START_FRAME)
    db = _db_with_configs(_config_row(2, 123))

    run_pipeline = await _run_external(ws, db, params)

    run_pipeline.assert_not_called()
    db.update_workflow_run.assert_not_called()
    assert ws.close.call_args.kwargs["code"] == 4401


@pytest.mark.asyncio
async def test_external_websocket_accepts_url_written_by_configure_inbound():
    # Round trip: the provider signs with an int client id, the handler checks
    # against the text client id in stored credentials.
    with patch.object(
        provider_module,
        "get_backend_endpoints",
        new_callable=AsyncMock,
        return_value=("https://api.example.test", "wss://api.example.test"),
    ):
        url = await _provider(api_token="t", client_id=123)._agent_stream_url(
            "28894e31-d767-45bc-b3f4-3c5b4e55950a", workflow_id=7
        )
    params = dict(parse_qsl(urlsplit(url).query))
    ws = _websocket(START_FRAME)
    db = _db_with_configs(_config_row(2, 123))

    run_pipeline = await _run_external(ws, db, params)

    ws.close.assert_not_called()
    run_pipeline.assert_awaited_once()


# ------------------------------------------------------- webhook-first flow


def test_never_claims_inbound_webhooks():
    assert VoiceLinkProvider.can_handle_webhook({"call_sid": "x"}, {}) is False


def test_validate_account_id_compares_client_id():
    assert VoiceLinkProvider.validate_account_id({"client_id": 123}, "123") is True
    assert VoiceLinkProvider.validate_account_id({"client_id": 123}, "999") is False
    assert VoiceLinkProvider.validate_account_id({}, "123") is False


def test_transfers_unsupported():
    assert _provider().supports_transfers() is False


# ------------------------------------------------------- automatic setup

BACKEND = ("https://api.example.test", "wss://api.example.test")
UNASSIGNED_URL = (
    "wss://api.example.test/api/v1/agent-stream/voicelink/"
    "00000000-0000-0000-0000-000000000000"
)


def _provisioner_mock(existing_bot=None) -> MagicMock:
    prov = MagicMock()
    prov.find_bot.return_value = existing_bot
    prov.ensure_bot.return_value = (SimpleNamespace(id=77), True)
    return prov


@pytest.mark.asyncio
async def test_provision_phone_number_creates_bot_and_routes_outbound():
    client_cls, client = _mock_sdk_client()
    client.dids.list.return_value = Page(items=[Did(id=1, did_number="910000000000")])
    prov = _provisioner_mock()
    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        patch.object(provider_module, "PipecatProvisioner", return_value=prov),
        patch.object(
            provider_module,
            "get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=BACKEND,
        ),
    ):
        result = await _provider().provision_phone_number(DID)

    assert result.ok is True
    prov.ensure_bot.assert_called_once_with(
        bot_name="dograh-910000000000", websocket_url=UNASSIGNED_URL
    )
    prov.route_outbound_to_bot.assert_called_once_with(did="910000000000", bot_id=77)
    prov.route_inbound_to_bot.assert_not_called()


@pytest.mark.asyncio
async def test_provision_phone_number_keeps_existing_bot_url():
    client_cls, client = _mock_sdk_client()
    client.dids.list.return_value = Page(items=[Did(id=1, did_number="910000000000")])
    prov = _provisioner_mock(existing_bot=SimpleNamespace(id=5))
    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        patch.object(provider_module, "PipecatProvisioner", return_value=prov),
        patch.object(
            provider_module,
            "get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=BACKEND,
        ),
    ):
        result = await _provider().provision_phone_number(DID)

    assert result.ok is True
    # Re-adding a number must not detach an agent that is still attached.
    prov.ensure_bot.assert_not_called()
    prov.route_outbound_to_bot.assert_called_once_with(did="910000000000", bot_id=5)


@pytest.mark.asyncio
async def test_provision_phone_number_refuses_unowned_number():
    client_cls, client = _mock_sdk_client()
    client.dids.list.return_value = Page(items=[])
    prov = _provisioner_mock()
    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        patch.object(provider_module, "PipecatProvisioner", return_value=prov),
    ):
        result = await _provider().provision_phone_number(DID)

    assert result.ok is False
    prov.ensure_bot.assert_not_called()
    prov.route_outbound_to_bot.assert_not_called()


@pytest.mark.asyncio
async def test_configure_inbound_points_bot_at_attached_agent():
    client_cls, _ = _mock_sdk_client()
    prov = _provisioner_mock()
    db = MagicMock()
    db.find_inbound_route_by_account = AsyncMock(
        return_value=(SimpleNamespace(id=2), SimpleNamespace(inbound_workflow_id=1))
    )
    db.get_workflow_by_id = AsyncMock(
        return_value=SimpleNamespace(
            id=1, workflow_uuid="28894e31-d767-45bc-b3f4-3c5b4e55950a"
        )
    )
    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        patch.object(provider_module, "PipecatProvisioner", return_value=prov),
        patch.object(provider_module, "db_client", db),
        patch.object(
            provider_module,
            "get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=BACKEND,
        ),
    ):
        result = await _provider().configure_inbound(
            DID, "https://api.example.test/api/v1/telephony/inbound/run"
        )

    assert result.ok is True
    db.find_inbound_route_by_account.assert_awaited_once_with(
        provider="voicelink",
        account_id_field="client_id",
        account_id="123",
        to_number=DID,
    )
    # Signed for the attached workflow (id 1) with this config's API token.
    token = VoiceLinkProvider._stream_token("token-123", 123, 1)
    prov.ensure_bot.assert_called_once_with(
        bot_name="dograh-910000000000",
        websocket_url=(
            "wss://api.example.test/api/v1/agent-stream/voicelink/"
            f"28894e31-d767-45bc-b3f4-3c5b4e55950a?vl_token={token}"
        ),
    )
    prov.route_inbound_to_bot.assert_called_once_with(did="910000000000", bot_id=77)


@pytest.mark.asyncio
async def test_configure_inbound_requires_client_id():
    client_cls, _ = _mock_sdk_client()
    prov = _provisioner_mock()
    db = MagicMock()
    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        patch.object(provider_module, "PipecatProvisioner", return_value=prov),
        patch.object(provider_module, "db_client", db),
    ):
        result = await _provider(client_id=None).configure_inbound(
            DID, "https://api.example.test/api/v1/telephony/inbound/run"
        )

    assert result.ok is False
    assert "client id" in (result.message or "")
    db.find_inbound_route_by_account.assert_not_called()
    db.find_inbound_route_by_called_number.assert_not_called()
    prov.ensure_bot.assert_not_called()


@pytest.mark.asyncio
async def test_configure_inbound_without_attached_agent_reports_error():
    client_cls, _ = _mock_sdk_client()
    prov = _provisioner_mock()
    db = MagicMock()
    db.find_inbound_route_by_account = AsyncMock(return_value=None)
    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        patch.object(provider_module, "PipecatProvisioner", return_value=prov),
        patch.object(provider_module, "db_client", db),
    ):
        result = await _provider().configure_inbound(
            DID, "https://api.example.test/api/v1/telephony/inbound/run"
        )

    assert result.ok is False
    assert "attached agent" in (result.message or "")
    prov.ensure_bot.assert_not_called()


@pytest.mark.asyncio
async def test_configure_inbound_detach_parks_bot():
    client_cls, _ = _mock_sdk_client()
    prov = _provisioner_mock()
    db = MagicMock()
    with (
        patch.object(provider_module, "VoiceLinkClient", client_cls),
        patch.object(provider_module, "PipecatProvisioner", return_value=prov),
        patch.object(provider_module, "db_client", db),
        patch.object(
            provider_module,
            "get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=BACKEND,
        ),
    ):
        result = await _provider().configure_inbound(DID, None)

    assert result.ok is True
    prov.ensure_bot.assert_called_once_with(
        bot_name="dograh-910000000000", websocket_url=UNASSIGNED_URL
    )
    db.find_inbound_route_by_account.assert_not_called()
