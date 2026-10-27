"""Caller-ID selection and safe retries for Twilio SIP transfers."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from xml.etree.ElementTree import fromstring

import aiohttp
import pytest
from loguru import logger

from api.services.telephony import call_transfer_manager
from api.services.telephony.providers.twilio import provider as provider_module
from api.services.telephony.providers.twilio.provider import TwilioProvider

ACCOUNT_NUMBER = "+15555550111"
CALLER_NUMBER = "+15555550222"
DESTINATION = "sip:reception@example.com"


def response(status, body):
    result = MagicMock(status=status)
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=False)
    result.text = AsyncMock(return_value=json.dumps(body))
    result.json = AsyncMock(return_value=body)
    return result


@pytest.fixture
def transfer(monkeypatch):
    provider = TwilioProvider(
        {
            "account_sid": "AC-run-account",
            "auth_token": "run-account-secret",
            "from_numbers": [ACCOUNT_NUMBER],
        }
    )
    manager = SimpleNamespace(
        get_transfer_context=AsyncMock(
            return_value=SimpleNamespace(original_call_sid="CA-original")
        )
    )
    monkeypatch.setattr(
        call_transfer_manager,
        "get_call_transfer_manager",
        AsyncMock(return_value=manager),
    )
    monkeypatch.setattr(
        provider_module,
        "get_backend_endpoints",
        AsyncMock(return_value=("https://api.example.com", "")),
    )
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get.return_value = response(
        200, {"direction": "inbound", "from": CALLER_NUMBER}
    )
    replies = [response(201, {"sid": "CA-destination", "status": "queued"})]
    requests = []

    def post(url, **kwargs):
        requests.append((url, deepcopy(kwargs)))
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    session.post.side_effect = post
    monkeypatch.setattr(provider_module.aiohttp, "ClientSession", lambda: session)
    return SimpleNamespace(
        provider=provider,
        manager=manager,
        session=session,
        replies=replies,
        requests=requests,
    )


async def dial(transfer, destination=DESTINATION, **kwargs):
    return await asyncio.wait_for(
        transfer.provider.transfer_call(
            destination=destination,
            transfer_id="tx-1",
            conference_name="transfer-conference",
            timeout=25,
            **kwargs,
        ),
        timeout=2,
    )


@pytest.mark.parametrize("destination", [DESTINATION, "SIP:desk@example.com"])
@pytest.mark.parametrize("introduction", [None, "https://api.example.com/audio"])
async def test_sip_transfer_preserves_inbound_caller(
    transfer, destination, introduction
):
    result = await dial(transfer, destination, introduction_audio_url=introduction)

    transfer.manager.get_transfer_context.assert_awaited_once_with("tx-1")
    transfer.session.get.assert_called_once_with(
        f"{transfer.provider.base_url}/Calls/CA-original.json",
        auth=aiohttp.BasicAuth("AC-run-account", "run-account-secret"),
    )
    assert len(transfer.requests) == 1
    url, request = transfer.requests[0]
    assert url == f"{transfer.provider.base_url}/Calls.json"
    assert request["auth"] == aiohttp.BasicAuth("AC-run-account", "run-account-secret")
    data = request["data"]
    assert data["From"] == CALLER_NUMBER
    assert data["To"] == destination
    assert data["Timeout"] == 25
    if introduction:
        assert "Twiml" not in data
        assert data["Url"].endswith("/twilio/transfer-introduction/tx-1")
        assert data["FallbackUrl"] == data["Url"] + "?skip_audio=true"
        assert data["StatusCallback"].endswith("/twilio/transfer-status/tx-1")
    else:
        assert (
            fromstring(data["Twiml"]).find("Dial/Conference").text
            == "transfer-conference"
        )
        assert data["StatusCallback"].endswith("/transfer-result/tx-1")
    assert result["call_sid"] == "CA-destination"
    assert result["from_number"] == CALLER_NUMBER


@pytest.mark.parametrize("introduction", [None, "https://api.example.com/audio"])
@pytest.mark.parametrize("status,code", [(400, 21210), (400, 21212), (403, 21210)])
async def test_caller_id_rejection_retries_once_with_account_number(
    transfer, introduction, status, code
):
    transfer.replies.insert(0, response(status, {"code": code}))
    result = await dial(transfer, introduction_audio_url=introduction)

    assert len(transfer.requests) == 2
    first, second = [request["data"] for _, request in transfer.requests]
    assert first["From"] == CALLER_NUMBER
    assert second == {**first, "From": ACCOUNT_NUMBER}
    assert result["from_number"] == ACCOUNT_NUMBER
    assert result["call_sid"] == "CA-destination"


async def test_rejected_fallback_does_not_dial_a_third_time(transfer):
    transfer.replies[:] = [response(400, {"code": 21210}) for _ in range(2)]
    with pytest.raises(RuntimeError, match="status 400: code=21210"):
        await dial(transfer)
    assert [request["data"]["From"] for _, request in transfer.requests] == [
        CALLER_NUMBER,
        ACCOUNT_NUMBER,
    ]


@pytest.mark.parametrize(
    "status,body",
    [
        (400, {"code": 21211}),  # Invalid destination, not caller ID.
        (401, {"code": 20003}),  # Authentication failure.
        (429, {"code": 20429}),  # Rate limit.
        (500, {"code": 21210}),  # Never retry an ambiguous server failure.
        (400, {}),
        (400, []),
        (400, "not-json"),
    ],
)
async def test_other_errors_do_not_retry(transfer, status, body):
    error = response(status, body)
    if body == "not-json":
        error.text.return_value = "<html>upstream error</html>"
    transfer.replies[:] = [error]
    with pytest.raises(RuntimeError, match=f"status {status}"):
        await dial(transfer)
    assert len(transfer.requests) == 1


@pytest.fixture
def error_logs():
    messages = []
    sink = logger.add(
        lambda message: messages.append(message.record["message"]), level="ERROR"
    )
    try:
        yield messages
    finally:
        logger.remove(sink)


@pytest.mark.parametrize(
    "status,code,explanation",
    [
        (400, 21211, "The destination is invalid"),
        (403, None, "Geographic calling permissions are disabled"),
        (502, None, "Upstream gateway unavailable"),
    ],
)
async def test_failure_explanation_is_logged_but_not_exposed_to_agent(
    transfer, error_logs, status, code, explanation
):
    error = response(status, {"code": code, "message": explanation})
    if status == 502:
        error.text.return_value = explanation
    transfer.replies[:] = [error]

    with pytest.raises(RuntimeError) as caught:
        await dial(transfer)

    assert (
        str(caught.value) == f"Twilio API call failed with status {status}: code={code}"
    )
    assert error_logs == [
        f"Twilio transfer tx-1 failed status={status} code={code} detail={explanation}"
    ]
    assert len(transfer.requests) == 1


async def test_failed_fallback_logs_its_own_explanation(transfer, error_logs):
    transfer.replies[:] = [
        response(400, {"code": 21210, "message": "Caller ID not verified"}),
        response(403, {"message": "Account is suspended"}),
    ]
    with pytest.raises(RuntimeError, match="status 403: code=None"):
        await dial(transfer)
    assert len(transfer.requests) == 2
    assert error_logs == [
        "Twilio transfer tx-1 failed status=403 code=None detail=Account is suspended"
    ]


@pytest.mark.parametrize("as_json", [True, False])
async def test_failure_log_redacts_addresses_and_secrets_before_truncation(
    transfer, error_logs, as_json
):
    secrets = [
        CALLER_NUMBER,
        ACCOUNT_NUMBER,
        DESTINATION,
        transfer.provider.auth_token,
        transfer.provider.account_sid,
        "bearer-secret",
        "query-secret",
        "quoted secret " * 60,
    ]
    explanation = (
        "Dial rejected: "
        + " ".join(secrets[:5])
        + " Authorization: Bearer bearer-secret; "
        + "https://example.com?token=query-secret "
        + 'password="'
        + secrets[-1]
        + '"\r\n'
        + "Details: "
        + "x" * 600
    )
    error = response(403, {"message": explanation, "request": "unrelated private data"})
    if not as_json:
        error.text.return_value = explanation
    transfer.replies[:] = [error]

    with pytest.raises(RuntimeError):
        await dial(transfer)

    assert len(error_logs) == 1
    message = error_logs[0]
    assert "Dial rejected:" in message
    assert "Details:" in message
    assert "[REDACTED]" in message
    assert "unrelated private data" not in message
    assert all(secret not in message for secret in secrets)
    assert "quoted secret" not in message
    assert "\n" not in message and "\r" not in message
    assert len(message.split(" detail=", 1)[1]) == 500


@pytest.mark.parametrize("message", [None, "", [], {"secret": "private data"}])
async def test_unusable_error_message_does_not_dump_response(
    transfer, error_logs, message
):
    transfer.replies[:] = [
        response(403, {"message": message, "request": "private data"})
    ]
    with pytest.raises(RuntimeError, match="status 403: code=None"):
        await dial(transfer)
    assert error_logs == [
        "Twilio transfer tx-1 failed status=403 code=None detail=No provider error explanation"
    ]


@pytest.mark.parametrize("error", [TimeoutError, aiohttp.ClientConnectionError])
async def test_uncertain_dial_result_is_not_retried(transfer, error):
    transfer.replies[:] = [error("connection lost")]
    with pytest.raises(error):
        await dial(transfer)
    assert len(transfer.requests) == 1


@pytest.mark.parametrize(
    "destination,kwargs,expected",
    [
        ("+15555550333", {}, ACCOUNT_NUMBER),
        (DESTINATION, {"From": "+15555550444"}, "+15555550444"),
    ],
)
async def test_pstn_and_explicit_caller_id_skip_lookup(
    transfer, destination, kwargs, expected
):
    result = await dial(transfer, destination, **kwargs)
    transfer.manager.get_transfer_context.assert_not_awaited()
    transfer.session.get.assert_not_called()
    assert transfer.requests[0][1]["data"]["From"] == expected
    assert result["from_number"] == expected


@pytest.mark.parametrize(
    "call",
    [
        {"direction": "outbound-api", "from": CALLER_NUMBER},
        {"direction": "inbound", "from": "anonymous"},
        {"direction": "inbound", "from": "restricted"},
        {"direction": "inbound", "from": "sip:caller@example.com"},
        {"direction": "inbound", "from": "+1555invalid"},
        {"direction": "inbound", "from": "+0123456789"},
        {"direction": "inbound", "from": "+1234567890123456"},
        {"direction": "inbound", "from": None},
        {},
        [],
    ],
)
async def test_unusable_original_caller_uses_account_number(transfer, call):
    transfer.session.get.return_value = response(200, call)
    result = await dial(transfer)
    assert transfer.requests[0][1]["data"]["From"] == ACCOUNT_NUMBER
    assert result["from_number"] == ACCOUNT_NUMBER


@pytest.mark.parametrize("context", [None, SimpleNamespace(original_call_sid="")])
async def test_missing_transfer_context_skips_call_lookup(transfer, context):
    transfer.manager.get_transfer_context.return_value = context
    result = await dial(transfer)
    transfer.session.get.assert_not_called()
    assert result["from_number"] == ACCOUNT_NUMBER


async def test_lookup_errors_preserve_transfer(transfer):
    transfer.session.get.return_value = response(404, {"code": 20404})
    result = await dial(transfer)
    assert result["from_number"] == ACCOUNT_NUMBER


@pytest.mark.parametrize(
    "error", [TimeoutError, aiohttp.ClientConnectionError, ValueError]
)
async def test_lookup_exceptions_preserve_transfer(transfer, error):
    transfer.session.get.return_value.json.side_effect = error("lookup failed")
    result = await dial(transfer)
    assert result["from_number"] == ACCOUNT_NUMBER


async def test_lookup_deadline_also_bounds_redis(transfer, monkeypatch):
    original_timeout = asyncio.timeout
    budgets = []

    def short_timeout(seconds):
        budgets.append(seconds)
        return original_timeout(0.01)

    async def blocked_context(*args):
        await asyncio.wait_for(asyncio.Event().wait(), timeout=1)

    monkeypatch.setattr(provider_module.asyncio, "timeout", short_timeout)
    transfer.manager.get_transfer_context.side_effect = blocked_context
    result = await dial(transfer)
    assert budgets == [5]
    transfer.session.get.assert_not_called()
    assert result["from_number"] == ACCOUNT_NUMBER


async def test_lookup_cancellation_stops_transfer(transfer):
    transfer.manager.get_transfer_context.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await dial(transfer)
    assert not transfer.requests


async def test_account_number_is_not_retried_if_it_was_already_used(transfer):
    transfer.session.get.return_value = response(
        200, {"direction": "inbound", "from": ACCOUNT_NUMBER}
    )
    transfer.replies[:] = [response(400, {"code": 21210})]
    with pytest.raises(RuntimeError, match="status 400: code=21210"):
        await dial(transfer)
    assert len(transfer.requests) == 1
