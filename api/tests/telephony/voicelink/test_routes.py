"""Status callback route tests for VoiceLink."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.enums import TelephonyCallStatus
from api.services.telephony.providers.voicelink.provider import VoiceLinkProvider
from api.services.telephony.providers.voicelink.routes import (
    handle_voicelink_status_callback,
)

ROUTES = "api.services.telephony.providers.voicelink.routes"
PATH = "/api/v1/telephony/voicelink/status-callback/42"

# Completed-but-unanswered Call Event webhook, shape captured live (numbers faked).
NO_ANSWER_WEBHOOK = {
    "event": "call.completed",
    "call": {
        "id": "0de3a0e3-5f8e-4f33-ba9f-f53e1a5711a3",
        "direction": "outbound",
        "from": "910000000000",
        "to": "910000000001",
        "status": "failed",
        "callStatus": "NO ANSWER",
        "durationSec": None,
        "customParameters": {"outboundQueueId": 300735},
    },
}


def _provider(**overrides) -> VoiceLinkProvider:
    config = {
        "api_token": "token-123",
        "client_id": "123",
        "from_numbers": ["+910000000000"],
    }
    config.update(overrides)
    return VoiceLinkProvider(config)


def _json_request(payload, *, query_string: bytes = b"") -> Request:
    body = (
        payload
        if isinstance(payload, (bytes, bytearray))
        else json.dumps(payload).encode("utf-8")
    )

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("example.test", 443),
            "path": PATH,
            "query_string": query_string,
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def _authed_request(provider: VoiceLinkProvider, payload, run_id: int = 42):
    token = provider._status_callback_token(run_id)
    return _json_request(payload, query_string=f"voicelink_auth={token}".encode())


_MISSING = object()


async def _call(
    request: Request,
    *,
    call_id="300735",
    provider=None,
    workflow_run=_MISSING,
    workflow=_MISSING,
):
    if workflow_run is _MISSING:
        workflow_run = SimpleNamespace(
            id=42, workflow_id=7, gathered_context={"call_id": call_id}
        )
    if workflow is _MISSING:
        workflow = SimpleNamespace(id=7, organization_id=9)
    with (
        patch(f"{ROUTES}.db_client") as db_client,
        patch(
            f"{ROUTES}.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider or _provider(),
        ),
        patch(f"{ROUTES}._process_status_update", new_callable=AsyncMock) as process,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        try:
            result = await handle_voicelink_status_callback(42, request)
        except HTTPException as exc:
            return exc, process
    return result, process


@pytest.mark.asyncio
async def test_no_answer_webhook_ends_run():
    result, process = await _call(_authed_request(_provider(), NO_ANSWER_WEBHOOK))

    assert result == {"status": "success"}
    process.assert_awaited_once()
    run_id, status = process.await_args.args
    assert run_id == 42
    # Matches the outbound_queue_id initiate_call recorded as the call id.
    assert status.call_id == "300735"
    assert status.status is TelephonyCallStatus.NO_ANSWER
    assert status.direction == "outbound"
    assert status.duration is None


@pytest.mark.asyncio
async def test_answered_webhook_passes_duration_as_text():
    payload = json.loads(json.dumps(NO_ANSWER_WEBHOOK))
    payload["call"].update(
        {"status": "completed", "callStatus": "ANSWERED", "durationSec": 31}
    )
    result, process = await _call(_authed_request(_provider(), payload))

    assert result == {"status": "success"}
    status = process.await_args.args[1]
    assert status.status is TelephonyCallStatus.COMPLETED
    assert status.duration == "31"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query_string",
    [
        b"",
        b"voicelink_auth=",
        b"voicelink_auth=" + b"0" * 64,
        # Token minted for another run.
        f"voicelink_auth={_provider()._status_callback_token(43)}".encode(),
        # Token minted by another configuration.
        f"voicelink_auth={_provider(api_token='x')._status_callback_token(42)}".encode(),
    ],
)
async def test_rejects_missing_or_forged_token(query_string):
    exc, process = await _call(
        _json_request(NO_ANSWER_WEBHOOK, query_string=query_string)
    )

    assert isinstance(exc, HTTPException)
    assert exc.status_code == 401
    process.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_webhook_for_another_call():
    exc, process = await _call(
        _authed_request(_provider(), NO_ANSWER_WEBHOOK), call_id="999999"
    )

    assert isinstance(exc, HTTPException)
    assert exc.status_code == 403
    process.assert_not_called()


def _with_echoed_run_id(run_id) -> dict:
    payload = json.loads(json.dumps(NO_ANSWER_WEBHOOK))
    payload["call"]["customParameters"]["workflow_run_id"] = run_id
    return payload


@pytest.mark.asyncio
async def test_early_callback_is_bound_by_echoed_run_id():
    # VoiceLink can report a failure after accepting the lead but before
    # initiate-call has persisted the queue id on the run. The signed URL proves
    # the run; the run id echoed from the lead's custom parameters binds it.
    exc_or_result, process = await _call(
        _authed_request(_provider(), _with_echoed_run_id(42)), call_id=None
    )

    assert exc_or_result == {"status": "success"}
    status = process.await_args.args[1]
    assert status.status is TelephonyCallStatus.NO_ANSWER
    assert status.call_id == "300735"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        NO_ANSWER_WEBHOOK,  # no echoed run id at all
        _with_echoed_run_id(43),  # echoed run id for a different run
    ],
)
async def test_early_callback_without_matching_run_id_is_rejected(payload):
    exc, process = await _call(_authed_request(_provider(), payload), call_id=None)

    assert isinstance(exc, HTTPException)
    assert exc.status_code == 403
    process.assert_not_called()


@pytest.mark.asyncio
async def test_recorded_call_id_still_wins_over_echoed_run_id():
    # Once the queue id is persisted, the echoed run id alone is not enough.
    exc, process = await _call(
        _authed_request(_provider(), _with_echoed_run_id(42)), call_id="999999"
    )

    assert isinstance(exc, HTTPException)
    assert exc.status_code == 403
    process.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [{"workflow_run": None}, {"workflow": None}])
async def test_unknown_run_looks_like_a_bad_token(missing):
    # Same 401 as an invalid token, so run ids cannot be enumerated.
    exc, process = await _call(
        _authed_request(_provider(), NO_ANSWER_WEBHOOK), **missing
    )

    assert isinstance(exc, HTTPException)
    assert exc.status_code == 401
    assert exc.detail == "Invalid webhook signature"
    process.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b"{}"])
async def test_ignores_malformed_body(body):
    result, process = await _call(_authed_request(_provider(), body))

    assert result == {"status": "ignored", "reason": "malformed_body"}
    process.assert_not_called()
