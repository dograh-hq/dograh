import asyncio
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import nacl.signing
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.services.telephony import ws_auth
from api.services.telephony.providers.telnyx import _ensure_connection_id
from api.services.telephony.providers.telnyx.provider import TelnyxProvider
from api.services.telephony.providers.telnyx.routes import handle_telnyx_events


def _event_body(event_type: str) -> str:
    return json.dumps(
        {
            "data": {
                "record_type": "event",
                "event_type": event_type,
                "payload": {
                    "call_control_id": "call-control-id",
                    "connection_id": "connection-id",
                    "direction": "incoming",
                    "from": "+15551230001",
                    "to": "+15551230002",
                },
            }
        },
        separators=(",", ":"),
    )


def _body() -> str:
    return _event_body("call.initiated")


def _provider(public_key: str = "") -> TelnyxProvider:
    return TelnyxProvider(
        {
            "api_key": "placeholder-api-key",
            "connection_id": "connection-id",
            "webhook_public_key": public_key,
            "from_numbers": ["+15551230002"],
        }
    )


def _signed_headers(body: str, timestamp: str | None = None):
    if timestamp is None:
        timestamp = str(int(time.time()))
    signing_key = nacl.signing.SigningKey.generate()
    public_key = base64.b64encode(bytes(signing_key.verify_key)).decode("ascii")
    signed_payload = f"{timestamp}|{body}".encode("utf-8")
    signature = base64.b64encode(signing_key.sign(signed_payload).signature).decode(
        "ascii"
    )
    return (
        public_key,
        {
            "telnyx-signature-ed25519": signature,
            "telnyx-timestamp": timestamp,
        },
    )


def _events_query_string(workflow_run_id: int = 123) -> bytes:
    """Query string carrying a valid capability token for *workflow_run_id*.

    Every request in this module targets the events route, which gates on the
    token whenever TELEPHONY_WS_TOKEN_SECRET is set -- as it is in production
    and in a developer's api/.env. Without this the tests below would only pass
    in the no-secret configuration and would 404 at the gate everywhere else.
    Returns b"" when no secret is configured, where the gate is skipped anyway.
    """
    token = ws_auth.mint_events_token(workflow_run_id)
    return f"token={token}".encode() if token else b""


def _request(body: str, headers: dict[str, str]) -> Request:
    async def receive():
        return {
            "type": "http.request",
            "body": body.encode("utf-8"),
            "more_body": False,
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/telephony/telnyx/events/123",
            "query_string": _events_query_string(),
            "headers": [
                (name.lower().encode("ascii"), value.encode("ascii"))
                for name, value in headers.items()
            ],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_verify_inbound_signature_accepts_valid_telnyx_signature():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is True


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_tampered_body():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body.replace("incoming", "outgoing"),
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_missing_signature_header():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        {"telnyx-timestamp": headers["telnyx-timestamp"]},
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_missing_timestamp_header():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        {"telnyx-signature-ed25519": headers["telnyx-signature-ed25519"]},
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_missing_config_public_key():
    body = _body()
    _, headers = _signed_headers(body)
    provider = _provider()

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_reads_headers_case_insensitively():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        {
            "Telnyx-Signature-Ed25519": headers["telnyx-signature-ed25519"],
            "Telnyx-Timestamp": headers["telnyx-timestamp"],
        },
        body,
    )

    assert result is True


@pytest.mark.asyncio
async def test_telnyx_events_route_verifies_signature_before_status_update():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    with (
        patch("api.services.telephony.providers.telnyx.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.telnyx.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.telnyx.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_telnyx_events(
            _request(body, headers), workflow_run_id=123
        )

    assert result == {"status": "success"}
    process_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_stale_timestamp():
    body = _body()
    stale_ts = str(int(time.time()) - 600)
    public_key, headers = _signed_headers(body, timestamp=stale_ts)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_future_timestamp():
    body = _body()
    future_ts = str(int(time.time()) + 600)
    public_key, headers = _signed_headers(body, timestamp=future_ts)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_non_integer_timestamp():
    body = _body()
    public_key, headers = _signed_headers(body)
    headers["telnyx-timestamp"] = "not-a-number"
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_wrong_length_public_key():
    body = _body()
    _, headers = _signed_headers(body)
    short_key = base64.b64encode(b"x" * 16).decode("ascii")
    provider = _provider(short_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_wrong_length_signature():
    body = _body()
    public_key, headers = _signed_headers(body)
    headers["telnyx-signature-ed25519"] = base64.b64encode(b"x" * 32).decode("ascii")
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_telnyx_events_route_rejects_invalid_signature_with_404():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    with (
        patch("api.services.telephony.providers.telnyx.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.telnyx.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.telnyx.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
        patch.object(
            provider,
            "verify_inbound_signature",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        with pytest.raises(HTTPException) as exc_info:
            await handle_telnyx_events(_request(body, headers), workflow_run_id=123)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Workflow run not found"
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_telnyx_events_route_rejects_invalid_utf8_body_with_400():
    invalid_body = b"\xff\xfe\xfd"

    async def receive():
        return {"type": "http.request", "body": invalid_body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/telephony/telnyx/events/123",
            "query_string": _events_query_string(),
            "headers": [],
        },
        receive,
    )

    with pytest.raises(HTTPException) as exc_info:
        await handle_telnyx_events(request, workflow_run_id=123)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Webhook body is not valid UTF-8"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        "streaming.started",
        "streaming.stopped",
        "call.recording.saved",
        "call.recording.error",
        "call.recording.transcription.saved",
    ],
)
async def test_telnyx_events_route_skips_informational_events_after_verification(
    event_type,
):
    body = _event_body(event_type)
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    with (
        patch("api.services.telephony.providers.telnyx.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.telnyx.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.telnyx.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_telnyx_events(
            _request(body, headers), workflow_run_id=123
        )

    assert result == {"status": "success"}
    process_status.assert_not_awaited()


def _fake_aiohttp_session(
    *,
    list_payload: dict | None = None,
    list_error: Exception | None = None,
    list_json_error: Exception | None = None,
    create_payload: dict | None = None,
) -> MagicMock:
    """Build an ``aiohttp.ClientSession`` mock for ``_ensure_connection_id``.

    The session supports the async-context-manager GET used to list
    Outbound Voice Profiles and the POST used to create the Call Control
    Application, mirroring the mock pattern used by the other telephony
    provider tests.
    """
    session = MagicMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False

    if list_error is not None:
        session.get.side_effect = list_error
    else:
        list_response = MagicMock()
        list_response.status = 200
        list_response.text = AsyncMock(
            return_value=json.dumps(list_payload, default=str)
        )
        if list_json_error is not None:
            list_response.json = AsyncMock(side_effect=list_json_error)
        else:
            list_response.json = AsyncMock(return_value=list_payload)
        session.get.return_value.__aenter__.return_value = list_response

    create_response = MagicMock()
    create_response.status = 201
    create_response.text = AsyncMock(return_value=json.dumps(create_payload))
    create_response.json = AsyncMock(return_value=create_payload)
    session.post.return_value.__aenter__.return_value = create_response

    return session


def _patch_ensure_connection_id(
    session: MagicMock,
):
    """Patch the two external seams of ``_ensure_connection_id``."""
    return (
        patch(
            "api.services.telephony.providers.telnyx.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.backend.test", "wss://api.backend.test"),
        ),
        patch(
            "api.services.telephony.providers.telnyx.aiohttp.ClientSession",
            MagicMock(return_value=session),
        ),
    )


@pytest.mark.asyncio
async def test_ensure_connection_id_attaches_outbound_voice_profile_id_when_one_exists():
    session = _fake_aiohttp_session(
        list_payload={"data": [{"id": "1234567890", "name": "Default Profile"}]},
        create_payload={"data": {"id": 111111, "application_name": "dograh-abc"}},
    )

    backend_patch, session_patch = _patch_ensure_connection_id(session)
    with backend_patch, session_patch:
        result = await _ensure_connection_id({"api_key": "placeholder-api-key"})

    assert result["connection_id"] == "111111"
    assert session.get.call_args.args[0].endswith("/outbound_voice_profiles")
    create_body = session.post.call_args.kwargs["json"]
    assert create_body["outbound"]["outbound_voice_profile_id"] == "1234567890"
    assert create_body["application_name"]
    assert "webhook_event_url" in create_body


@pytest.mark.asyncio
async def test_ensure_connection_id_omits_outbound_when_no_voice_profiles_exist():
    session = _fake_aiohttp_session(
        list_payload={"data": []},
        create_payload={"data": {"id": 111111, "application_name": "dograh-abc"}},
    )

    backend_patch, session_patch = _patch_ensure_connection_id(session)
    with backend_patch, session_patch:
        result = await _ensure_connection_id({"api_key": "placeholder-api-key"})

    assert result["connection_id"] == "111111"
    create_body = session.post.call_args.kwargs["json"]
    assert "outbound" not in create_body


@pytest.mark.asyncio
async def test_ensure_connection_id_omits_outbound_when_profile_list_call_raises():
    session = _fake_aiohttp_session(
        list_error=aiohttp.ClientError("connection reset"),
        create_payload={"data": {"id": 111111, "application_name": "dograh-abc"}},
    )

    backend_patch, session_patch = _patch_ensure_connection_id(session)
    with backend_patch, session_patch:
        result = await _ensure_connection_id({"api_key": "placeholder-api-key"})

    assert result["connection_id"] == "111111"
    create_body = session.post.call_args.kwargs["json"]
    assert "outbound" not in create_body


@pytest.mark.asyncio
async def test_ensure_connection_id_omits_outbound_when_profile_list_call_fails_http():
    session = MagicMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False

    list_response = MagicMock()
    list_response.status = 401
    list_response.text = AsyncMock(return_value='{"errors": ["unauthorized"]}')
    list_response.json = AsyncMock(return_value={"errors": ["unauthorized"]})
    session.get.return_value.__aenter__.return_value = list_response

    create_response = MagicMock()
    create_response.status = 201
    create_response.text = AsyncMock(return_value='{"data": {"id": 111111}}')
    create_response.json = AsyncMock(return_value={"data": {"id": 111111}})
    session.post.return_value.__aenter__.return_value = create_response

    backend_patch, session_patch = _patch_ensure_connection_id(session)
    with backend_patch, session_patch:
        result = await _ensure_connection_id({"api_key": "placeholder-api-key"})

    assert result["connection_id"] == "111111"
    create_body = session.post.call_args.kwargs["json"]
    assert "outbound" not in create_body


@pytest.mark.asyncio
async def test_ensure_connection_id_omits_outbound_when_profile_list_times_out():
    """A profile-list timeout must not abort the config save (contract: the
    save always succeeds so inbound calls work). asyncio.TimeoutError is not
    an aiohttp.ClientError subclass, so it is caught explicitly."""
    session = _fake_aiohttp_session(
        list_error=asyncio.TimeoutError("profile GET timed out"),
        create_payload={"data": {"id": 111111, "application_name": "dograh-abc"}},
    )

    backend_patch, session_patch = _patch_ensure_connection_id(session)
    with backend_patch, session_patch:
        result = await _ensure_connection_id({"api_key": "placeholder-api-key"})

    assert result["connection_id"] == "111111"
    create_body = session.post.call_args.kwargs["json"]
    assert "outbound" not in create_body


@pytest.mark.asyncio
async def test_ensure_connection_id_tolerates_unexpected_profile_payload_shapes():
    """Non-dict payloads, non-list 'data', and non-dict entries must all be
    treated as 'no profiles available' instead of raising."""
    for bad_payload in ([], {"data": [None]}, {"data": "not-a-list"}, {"other": 1}):
        session = _fake_aiohttp_session(
            list_payload=bad_payload,
            create_payload={"data": {"id": 111111, "application_name": "dograh-abc"}},
        )

        backend_patch, session_patch = _patch_ensure_connection_id(session)
        with backend_patch, session_patch:
            result = await _ensure_connection_id({"api_key": "placeholder-api-key"})

        assert result["connection_id"] == "111111", f"payload {bad_payload!r}"
        create_body = session.post.call_args.kwargs["json"]
        assert "outbound" not in create_body, f"payload {bad_payload!r}"


@pytest.mark.asyncio
async def test_ensure_connection_id_omits_outbound_when_profile_list_body_is_malformed():
    """A 200 with a JSON content type but an unparseable body raises
    json.JSONDecodeError — a ValueError, not an aiohttp.ClientError. It must
    not escape and 500 the config save (contract: the save always succeeds)."""
    session = _fake_aiohttp_session(
        list_json_error=json.JSONDecodeError("Expecting value", "", 0),
        create_payload={"data": {"id": 111111, "application_name": "dograh-abc"}},
    )

    backend_patch, session_patch = _patch_ensure_connection_id(session)
    with backend_patch, session_patch:
        result = await _ensure_connection_id({"api_key": "placeholder-api-key"})

    assert result["connection_id"] == "111111"
    create_body = session.post.call_args.kwargs["json"]
    assert "outbound" not in create_body


@pytest.mark.asyncio
async def test_ensure_connection_id_skips_disabled_outbound_voice_profiles():
    """A disabled profile still fails the dial, so it is no better than none:
    the first *enabled* profile is bound instead."""
    session = _fake_aiohttp_session(
        list_payload={
            "data": [
                {"id": "disabled-1", "name": "Retired", "enabled": False},
                {"id": "enabled-2", "name": "Live", "enabled": True},
            ]
        },
        create_payload={"data": {"id": 111111, "application_name": "dograh-abc"}},
    )

    backend_patch, session_patch = _patch_ensure_connection_id(session)
    with backend_patch, session_patch:
        await _ensure_connection_id({"api_key": "placeholder-api-key"})

    create_body = session.post.call_args.kwargs["json"]
    assert create_body["outbound"]["outbound_voice_profile_id"] == "enabled-2"


@pytest.mark.asyncio
async def test_ensure_connection_id_omits_outbound_when_every_profile_is_disabled():
    session = _fake_aiohttp_session(
        list_payload={"data": [{"id": "disabled-1", "enabled": False}]},
        create_payload={"data": {"id": 111111, "application_name": "dograh-abc"}},
    )

    backend_patch, session_patch = _patch_ensure_connection_id(session)
    with backend_patch, session_patch:
        result = await _ensure_connection_id({"api_key": "placeholder-api-key"})

    assert result["connection_id"] == "111111"
    create_body = session.post.call_args.kwargs["json"]
    assert "outbound" not in create_body


@pytest.mark.asyncio
async def test_ensure_connection_id_profile_lookup_is_bounded_by_a_timeout():
    """aiohttp's default total timeout is 5 minutes; the best-effort profile
    lookup must not be able to stall a config save for that long."""
    session = _fake_aiohttp_session(
        list_payload={"data": [{"id": "1234567890"}]},
        create_payload={"data": {"id": 111111, "application_name": "dograh-abc"}},
    )

    backend_patch, session_patch = _patch_ensure_connection_id(session)
    with backend_patch, session_patch:
        await _ensure_connection_id({"api_key": "placeholder-api-key"})

    timeout = session.get.call_args.kwargs["timeout"]
    assert timeout.total is not None and timeout.total <= 30


def test_informational_event_types_match_telnyx_catalog():
    """Guards the two mistakes this list invites: inventing an event Telnyx
    never sends, and forgetting one that it does.

    Source: Telnyx's published webhook catalog at
    https://developers.telnyx.com/data/webhook-events.json — the call.* events
    for recording are saved / error / transcription.saved. There is no
    call.recording.started.
    """
    from api.services.telephony.providers.telnyx.routes import (
        _INFORMATIONAL_EVENT_TYPES,
    )

    assert "call.recording.started" not in _INFORMATIONAL_EVENT_TYPES
    assert {
        "call.recording.saved",
        "call.recording.error",
        "call.recording.transcription.saved",
    } <= _INFORMATIONAL_EVENT_TYPES
    # Streaming events keep their pre-existing skip.
    assert {"streaming.started", "streaming.stopped"} <= _INFORMATIONAL_EVENT_TYPES
