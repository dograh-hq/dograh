"""Tests for the Dial telephony provider."""

import asyncio
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pipecat.audio import g711
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    InputDTMFFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
)
from starlette.websockets import WebSocketDisconnect

import api.services.telephony.providers.dial as dial_package
from api.enums import TelephonyCallStatus, WorkflowRunState
from api.services.telephony.providers.dial.client import DialAPIError
from api.services.telephony.providers.dial.provider import DialProvider
from api.services.telephony.providers.dial.routes import router
from api.services.telephony.providers.dial.serializers import DialFrameSerializer
from api.services.telephony.providers.dial.signatures import (
    sign_for_tests,
    verify_dial_signature,
)
from api.services.telephony.providers.dial.websocket import DialWebSocket

ACCOUNT_ID = "acct_123"
AUDIO_SECRET = "audio-secret"
WEBHOOK_SECRET = "whsec_test"
CALL_ID = "call_01HW3X7P5QF6K2YQ9YJ7Q8R5N3"


def _credentials(**overrides):
    return {
        "provider": "dial",
        "api_key": "sk_live_test",
        "account_id": ACCOUNT_ID,
        "audio_signing_secret": AUDIO_SECRET,
        "webhook_id": "wh_1",
        "webhook_secret": WEBHOOK_SECRET,
        **overrides,
    }


def _provider(**overrides):
    return DialProvider(
        {**_credentials(), "from_numbers": ["+14155559876"], **overrides}
    )


# --------------------------------------------------------------------------
# Signatures
# --------------------------------------------------------------------------


def test_signature_round_trip():
    now = int(time.time())
    header = sign_for_tests(AUDIO_SECRET, CALL_ID, now)
    assert verify_dial_signature(AUDIO_SECRET, header, CALL_ID)


def test_signature_rejects_wrong_secret_data_and_stale_timestamp():
    now = int(time.time())
    header = sign_for_tests(AUDIO_SECRET, CALL_ID, now)
    assert not verify_dial_signature("other", header, CALL_ID)
    assert not verify_dial_signature(AUDIO_SECRET, header, "call_other")
    stale = sign_for_tests(AUDIO_SECRET, CALL_ID, now - 3600)
    assert not verify_dial_signature(AUDIO_SECRET, stale, CALL_ID)
    assert not verify_dial_signature(AUDIO_SECRET, None, CALL_ID)
    assert not verify_dial_signature(None, header, CALL_ID)
    assert not verify_dial_signature(AUDIO_SECRET, "garbage", CALL_ID)


# --------------------------------------------------------------------------
# Serializer
# --------------------------------------------------------------------------


async def _serializer():
    serializer = DialFrameSerializer(call_id=CALL_ID)
    # setup() only reads the pipeline's input rate.
    await serializer.setup(SimpleNamespace(audio_in_sample_rate=8000))
    return serializer


async def test_serializer_decodes_caller_media():
    serializer = await _serializer()
    pcm = b"\x00\x10" * 160
    payload = base64.b64encode(g711.pcm_to_ulaw(pcm)).decode()

    frame = await serializer.deserialize(
        json.dumps({"type": "media", "payload": payload, "seq": 1})
    )

    assert isinstance(frame, InputAudioRawFrame)
    assert frame.sample_rate == 8000
    assert len(frame.audio) == len(pcm)


async def test_serializer_encodes_agent_media_as_mulaw():
    serializer = await _serializer()
    pcm = b"\x00\x10" * 160

    out = json.loads(
        await serializer.serialize(
            OutputAudioRawFrame(audio=pcm, sample_rate=8000, num_channels=1)
        )
    )

    assert out["type"] == "media"
    assert len(base64.b64decode(out["payload"])) == 160  # 1 byte per μ-law sample


async def test_serializer_maps_dtmf_and_ignores_lifecycle_frames():
    serializer = await _serializer()
    dtmf = await serializer.deserialize(json.dumps({"type": "dtmf", "digit": "#"}))
    assert isinstance(dtmf, InputDTMFFrame)
    assert dtmf.button.value == "#"

    for frame in (
        {"type": "call_ended", "reason": "caller_hangup"},
        {"type": "duration_warning", "seconds_remaining": 30},
        {"type": "mark", "name": "x"},
        {"type": "dtmf", "digit": "x"},
    ):
        assert await serializer.deserialize(json.dumps(frame)) is None
    assert await serializer.deserialize("not json") is None


async def test_serializer_sends_clear_on_interruption_and_end_call_once():
    serializer = await _serializer()
    assert json.loads(await serializer.serialize(InterruptionFrame())) == {
        "type": "clear"
    }
    assert json.loads(await serializer.serialize(EndFrame())) == {"type": "end_call"}
    assert await serializer.serialize(CancelFrame()) is None


# --------------------------------------------------------------------------
# Keepalive socket wrapper
# --------------------------------------------------------------------------


class _FakeSocket:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    async def receive(self):
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(3600)

    async def send_text(self, text):
        self.sent.append(text)


async def test_wrapper_echoes_ping_pong_and_queues_everything_else():
    ping = json.dumps({"type": "ping_pong", "timestamp": 1749480000000})
    media = json.dumps({"type": "media", "payload": "AA==", "seq": 1})
    fake = _FakeSocket(
        [
            {"type": "websocket.receive", "text": ping},
            {"type": "websocket.receive", "text": media},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    socket = DialWebSocket(fake)
    socket.start()

    first = await asyncio.wait_for(socket.receive(), 1)
    second = await asyncio.wait_for(socket.receive(), 1)

    assert first["text"] == media
    assert second["type"] == "websocket.disconnect"
    assert fake.sent == [ping]
    # Once closed, further reads keep reporting the disconnect.
    assert (await socket.receive())["type"] == "websocket.disconnect"
    await socket.stop()


async def test_wrapper_keeps_only_recent_frames_while_nobody_reads():
    frames = [
        {"type": "websocket.receive", "text": json.dumps({"type": "media", "seq": i})}
        for i in range(200)
    ]
    socket = DialWebSocket(_FakeSocket(frames))
    socket.start()
    await asyncio.sleep(0.05)

    first = json.loads((await socket.receive())["text"])
    assert first["seq"] == 150
    await socket.stop()


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------


def test_parse_status_callback_for_call_ended():
    parsed = _provider().parse_status_callback(
        {
            "type": "call.ended",
            "data": {
                "callId": CALL_ID,
                "from": "+14155559876",
                "to": "+14155550123",
                "direction": "outbound",
                "durationSeconds": None,
                "status": "no-answer",
            },
        }
    )
    assert parsed["call_id"] == CALL_ID
    assert parsed["status"] == TelephonyCallStatus.NO_ANSWER
    assert parsed["duration"] is None


def test_parse_status_callback_for_status_changed():
    provider = _provider()
    answered = provider.parse_status_callback(
        {
            "type": "call.status_changed",
            "data": {"callId": CALL_ID, "status": {"state": "In-Progress"}},
        }
    )
    ended = provider.parse_status_callback(
        {
            "type": "call.status_changed",
            "data": {
                "callId": CALL_ID,
                "status": {"state": "Terminated"},
                "terminationType": "busy",
            },
        }
    )
    assert answered["status"] == TelephonyCallStatus.IN_PROGRESS
    assert ended["status"] == TelephonyCallStatus.BUSY


def test_parse_inbound_call_connected():
    normalized = DialProvider.parse_inbound_webhook(
        {
            "type": "call_connected",
            "call_id": CALL_ID,
            "direction": "inbound",
            "from": "+14155550123",
            "to": "+14155559876",
            "account_id": ACCOUNT_ID,
        }
    )
    assert normalized.provider == "dial"
    assert normalized.call_id == CALL_ID
    assert normalized.to_number == "+14155559876"
    assert normalized.account_id == ACCOUNT_ID


async def test_initiate_call_places_an_idempotent_dial_call():
    provider = _provider()
    make_call = AsyncMock(
        return_value={"id": CALL_ID, "status": "queued", "from": "+14155559876"}
    )
    with patch(
        "api.services.telephony.providers.dial.provider.DialClient.make_call",
        make_call,
    ):
        result = await provider.initiate_call(
            to_number="+14155550123", webhook_url="unused", workflow_run_id=42
        )

    body = make_call.await_args.args[0]
    assert body["to"] == "+14155550123"
    assert body["fromNumber"] == "+14155559876"
    assert body["outboundInstruction"]
    assert make_call.await_args.kwargs["idempotency_key"] == "dograh-run-42"
    assert result.call_id == CALL_ID
    assert result.provider_metadata == {"call_id": CALL_ID}


async def test_initiate_call_surfaces_dial_errors():
    provider = _provider()
    with patch(
        "api.services.telephony.providers.dial.provider.DialClient.make_call",
        AsyncMock(side_effect=DialAPIError("calling_disabled", status_code=409)),
    ):
        with pytest.raises(HTTPException) as exc:
            await provider.initiate_call(to_number="+14155550123", webhook_url="x")
    assert exc.value.status_code == 409


async def test_list_provider_phone_numbers_keeps_only_voice_numbers():
    numbers = [
        {"id": "n1", "number": "+14155559876", "capabilities": ["sms", "call"]},
        {"id": "n2", "number": "+14155550000", "capabilities": ["whatsapp"]},
        {
            "id": "n3",
            "number": "+14155551111",
            "capabilities": ["sms", "call"],
            "callingEnabled": False,
        },
    ]
    with patch(
        "api.services.telephony.providers.dial.provider.DialClient.list_numbers",
        AsyncMock(return_value=numbers),
    ):
        listed = await _provider().list_provider_phone_numbers()
        missing = await _provider().validate_phone_number("+14155550000")
        owned = await _provider().validate_phone_number("+14155559876")

    assert [n.address for n in listed] == ["+14155559876"]
    assert not missing.ok
    assert owned.ok


# --------------------------------------------------------------------------
# Save hook
# --------------------------------------------------------------------------


class _FakeDialClient:
    def __init__(self, *, access="granted", webhooks=None, account_id=ACCOUNT_ID):
        self.access = access
        self.webhooks = list(webhooks or [])
        self.account_id = account_id
        self.calls = []

    def __call__(self, api_key):
        return self

    async def get_account(self):
        return {"id": self.account_id}

    async def get_self_hosted(self):
        return {"access": self.access, "enabled": False, "activeMode": "llm"}

    async def request_self_hosted_access(self, use_case, company_url=None):
        self.calls.append(("request_access", use_case))
        return {"access": "pending"}

    async def activate_self_hosted_audio(self, ws_url, audio_format):
        self.calls.append(("activate", ws_url, audio_format))
        return {"enabled": True}

    async def get_self_hosted_secret(self, mode="audio"):
        return AUDIO_SECRET

    async def list_webhooks(self):
        return self.webhooks

    async def create_webhook(self, target_url, event_types):
        self.calls.append(("create_webhook", target_url, event_types))
        return {"id": "wh_new", "targetUrl": target_url, "secret": WEBHOOK_SECRET}

    async def get_webhook_secret(self, webhook_id):
        return f"secret-for-{webhook_id}"


def _patch_setup(fake):
    return (
        patch.object(dial_package, "DialClient", fake),
        patch.object(
            dial_package,
            "get_backend_endpoints",
            AsyncMock(return_value=("https://dograh.example", "wss://dograh.example")),
        ),
    )


async def test_save_activates_self_hosted_audio_and_creates_webhook():
    fake = _FakeDialClient()
    client_patch, endpoints_patch = _patch_setup(fake)
    with client_patch, endpoints_patch:
        saved = await dial_package._ensure_dial_setup(
            {"provider": "dial", "api_key": "sk_live_test"}
        )

    assert saved["account_id"] == ACCOUNT_ID
    assert saved["audio_signing_secret"] == AUDIO_SECRET
    assert saved["webhook_id"] == "wh_new"
    assert saved["webhook_secret"] == WEBHOOK_SECRET
    assert (
        "activate",
        f"wss://dograh.example/api/v1/telephony/dial/ws/{ACCOUNT_ID}",
        "mulaw_8000",
    ) in fake.calls
    assert (
        "create_webhook",
        f"https://dograh.example/api/v1/telephony/dial/events/{ACCOUNT_ID}",
        ["call.ended"],
    ) in fake.calls


async def test_save_reuses_the_existing_webhook():
    target = f"https://dograh.example/api/v1/telephony/dial/events/{ACCOUNT_ID}"
    fake = _FakeDialClient(webhooks=[{"id": "wh_1", "targetUrl": target}])
    client_patch, endpoints_patch = _patch_setup(fake)
    with client_patch, endpoints_patch:
        saved = await dial_package._ensure_dial_setup(_credentials())

    assert saved["webhook_id"] == "wh_1"
    assert saved["webhook_secret"] == WEBHOOK_SECRET
    assert not [c for c in fake.calls if c[0] == "create_webhook"]


async def test_save_with_a_key_for_another_account_drops_derived_secrets():
    target = "https://dograh.example/api/v1/telephony/dial/events/acct_new"
    fake = _FakeDialClient(
        account_id="acct_new", webhooks=[{"id": "wh_9", "targetUrl": target}]
    )
    client_patch, endpoints_patch = _patch_setup(fake)
    with client_patch, endpoints_patch:
        saved = await dial_package._ensure_dial_setup(_credentials())

    assert saved["account_id"] == "acct_new"
    assert saved["webhook_id"] == "wh_9"
    assert saved["webhook_secret"] == "secret-for-wh_9"


async def test_save_requests_self_hosted_access_when_missing():
    fake = _FakeDialClient(access="none")
    client_patch, endpoints_patch = _patch_setup(fake)
    with client_patch, endpoints_patch, pytest.raises(HTTPException) as exc:
        await dial_package._ensure_dial_setup({"api_key": "sk_live_test"})

    assert exc.value.status_code == 400
    assert "requested access" in exc.value.detail
    assert fake.calls[0][0] == "request_access"
    assert not [c for c in fake.calls if c[0] == "activate"]


async def test_save_explains_a_pending_access_request():
    fake = _FakeDialClient(access="pending")
    client_patch, endpoints_patch = _patch_setup(fake)
    with client_patch, endpoints_patch, pytest.raises(HTTPException) as exc:
        await dial_package._ensure_dial_setup({"api_key": "sk_live_test"})
    assert "under review" in exc.value.detail


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


def _app():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/telephony")
    return app


def _config(**overrides):
    return SimpleNamespace(
        id=55,
        organization_id=11,
        credentials=_credentials(),
        **overrides,
    )


def _run(**overrides):
    defaults = dict(
        id=901,
        workflow_id=33,
        state=WorkflowRunState.INITIALIZED.value,
        initial_context={"provider": "dial", "telephony_configuration_id": 55},
        workflow=SimpleNamespace(organization_id=11),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _connected(direction="outbound", **extra):
    return {
        "type": "call_connected",
        "call_id": CALL_ID,
        "direction": direction,
        "from": "+14155559876" if direction == "outbound" else "+14155550123",
        "to": "+14155550123" if direction == "outbound" else "+14155559876",
        "instruction": None,
        "language": "en-US",
        "formats": {"inbound": "mulaw_8000", "outbound": "mulaw_8000"},
        **extra,
    }


def _signed_headers(secret=AUDIO_SECRET):
    return {"X-Dial-Signature": sign_for_tests(secret, CALL_ID, int(time.time()))}


def test_outbound_socket_runs_the_pipeline_for_the_placed_call():
    pipeline = AsyncMock()
    with (
        patch("api.services.telephony.providers.dial.routes.db_client") as db,
        patch(
            "api.services.telephony.providers.dial.routes.get_telephony_provider_for_run",
            AsyncMock(return_value=_provider()),
        ),
        patch("api.services.pipecat.run_pipeline.run_pipeline_telephony", pipeline),
    ):
        db.list_active_telephony_configurations_by_provider = AsyncMock(
            return_value=[_config()]
        )
        db.get_workflow_run_by_call_id = AsyncMock(return_value=_run())

        with TestClient(_app()).websocket_connect(
            f"/api/v1/telephony/dial/ws/{ACCOUNT_ID}/{CALL_ID}",
            headers=_signed_headers(),
        ) as ws:
            ws.send_json(_connected())

    kwargs = pipeline.await_args.kwargs
    assert kwargs["provider_name"] == "dial"
    assert kwargs["workflow_run_id"] == 901
    assert kwargs["organization_id"] == 11
    assert kwargs["call_id"] == CALL_ID
    assert kwargs["transport_kwargs"] == {"call_id": CALL_ID}
    assert isinstance(pipeline.await_args.args[0], DialWebSocket)


def test_socket_with_a_bad_signature_is_closed_without_touching_runs():
    pipeline = AsyncMock()
    with (
        patch("api.services.telephony.providers.dial.routes.db_client") as db,
        patch("api.services.pipecat.run_pipeline.run_pipeline_telephony", pipeline),
    ):
        db.list_active_telephony_configurations_by_provider = AsyncMock(
            return_value=[_config()]
        )
        db.get_workflow_run_by_call_id = AsyncMock(return_value=_run())

        with TestClient(_app()).websocket_connect(
            f"/api/v1/telephony/dial/ws/{ACCOUNT_ID}/{CALL_ID}",
            headers=_signed_headers("wrong-secret"),
        ) as ws:
            ws.send_json(_connected())
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()

    assert exc.value.code == 4401
    db.get_workflow_run_by_call_id.assert_not_awaited()
    pipeline.assert_not_awaited()


def test_reconnect_is_hung_up():
    with patch("api.services.telephony.providers.dial.routes.db_client") as db:
        db.list_active_telephony_configurations_by_provider = AsyncMock(
            return_value=[_config()]
        )
        with TestClient(_app()).websocket_connect(
            f"/api/v1/telephony/dial/ws/{ACCOUNT_ID}/{CALL_ID}",
            headers=_signed_headers(),
        ) as ws:
            ws.send_json(_connected(reconnect=True))
            assert ws.receive_json() == {"type": "end_call"}
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
    assert exc.value.code == 4409


def test_inbound_socket_creates_a_run_and_starts_the_pipeline():
    pipeline = AsyncMock()
    phone_row = SimpleNamespace(id=7, inbound_workflow_id=33)
    workflow = SimpleNamespace(id=33, user_id=99, organization_id=11)
    with (
        patch("api.services.telephony.providers.dial.routes.db_client") as db,
        patch(
            "api.services.telephony.providers.dial.routes.get_telephony_provider_by_id",
            AsyncMock(return_value=_provider()),
        ),
        patch(
            "api.services.telephony.providers.dial.routes.call_concurrency"
        ) as concurrency,
        patch(
            "api.services.telephony.providers.dial.routes.prepare_workflow_run_inputs",
            AsyncMock(return_value=SimpleNamespace(definition_id=5, use_draft=False)),
        ),
        patch(
            "api.services.telephony.providers.dial.routes.authorize_workflow_run_start",
            AsyncMock(return_value=SimpleNamespace(has_quota=True)),
        ),
        patch("api.services.pipecat.run_pipeline.run_pipeline_telephony", pipeline),
    ):
        db.list_active_telephony_configurations_by_provider = AsyncMock(
            return_value=[_config()]
        )
        db.find_inbound_route_by_account = AsyncMock(
            return_value=(_config(), phone_row)
        )
        db.get_workflow_run_by_call_id = AsyncMock(return_value=None)
        db.get_workflow = AsyncMock(return_value=workflow)
        db.create_workflow_run = AsyncMock(return_value=SimpleNamespace(id=902))
        concurrency.acquire_org_slot = AsyncMock(return_value="slot")
        concurrency.bind_workflow_run = AsyncMock()

        with TestClient(_app()).websocket_connect(
            f"/api/v1/telephony/dial/ws/{ACCOUNT_ID}/{CALL_ID}",
            headers=_signed_headers(),
        ) as ws:
            ws.send_json(_connected("inbound"))

    route_kwargs = db.find_inbound_route_by_account.await_args.kwargs
    assert route_kwargs["account_id"] == ACCOUNT_ID
    assert route_kwargs["to_number"] == "+14155559876"

    create = db.create_workflow_run.await_args
    assert create.args[2] == "dial"
    assert create.kwargs["gathered_context"] == {"call_id": CALL_ID}
    assert create.kwargs["initial_context"]["caller_number"] == "+14155550123"
    assert create.kwargs["initial_context"]["telephony_configuration_id"] == 55
    concurrency.bind_workflow_run.assert_awaited_once_with("slot", 902)
    assert pipeline.await_args.kwargs["workflow_run_id"] == 902


def test_inbound_call_to_a_number_without_a_workflow_is_hung_up():
    with (
        patch("api.services.telephony.providers.dial.routes.db_client") as db,
        patch(
            "api.services.telephony.providers.dial.routes.get_telephony_provider_by_id",
            AsyncMock(return_value=_provider()),
        ),
    ):
        db.list_active_telephony_configurations_by_provider = AsyncMock(
            return_value=[_config()]
        )
        db.find_inbound_route_by_account = AsyncMock(
            return_value=(_config(), SimpleNamespace(id=7, inbound_workflow_id=None))
        )
        db.get_workflow_run_by_call_id = AsyncMock(return_value=None)

        with TestClient(_app()).websocket_connect(
            f"/api/v1/telephony/dial/ws/{ACCOUNT_ID}/{CALL_ID}",
            headers=_signed_headers(),
        ) as ws:
            ws.send_json(_connected("inbound"))
            assert ws.receive_json() == {"type": "end_call"}


def _signed_event(event):
    body = json.dumps(event)
    return body, {
        "X-Dial-Signature": sign_for_tests(WEBHOOK_SECRET, body, int(time.time())),
        "Content-Type": "application/json",
    }


def test_call_ended_event_updates_the_run():
    process = AsyncMock()
    event = {
        "id": "evt_1",
        "type": "call.ended",
        "data": {"callId": CALL_ID, "status": "busy", "durationSeconds": None},
    }
    body, headers = _signed_event(event)
    with (
        patch("api.services.telephony.providers.dial.routes.db_client") as db,
        patch(
            "api.services.telephony.providers.dial.routes.get_telephony_provider_for_run",
            AsyncMock(return_value=_provider()),
        ),
        patch(
            "api.services.telephony.providers.dial.routes._process_status_update",
            process,
        ),
    ):
        db.list_active_telephony_configurations_by_provider = AsyncMock(
            return_value=[_config()]
        )
        db.get_workflow_run_by_call_id = AsyncMock(return_value=_run())

        response = TestClient(_app()).post(
            f"/api/v1/telephony/dial/events/{ACCOUNT_ID}", content=body, headers=headers
        )

    assert response.status_code == 200
    run_id, update = process.await_args.args
    assert run_id == 901
    assert update.call_id == CALL_ID
    assert update.status == TelephonyCallStatus.BUSY


def test_unsigned_event_is_rejected_like_an_unknown_account():
    body, _ = _signed_event({"type": "call.ended", "data": {"callId": CALL_ID}})
    with patch("api.services.telephony.providers.dial.routes.db_client") as db:
        db.list_active_telephony_configurations_by_provider = AsyncMock(
            return_value=[_config()]
        )
        db.get_workflow_run_by_call_id = AsyncMock()
        response = TestClient(_app()).post(
            f"/api/v1/telephony/dial/events/{ACCOUNT_ID}",
            content=body,
            headers={"X-Dial-Signature": "t=1,v1=00"},
        )

    assert response.status_code == 404
    db.get_workflow_run_by_call_id.assert_not_awaited()
