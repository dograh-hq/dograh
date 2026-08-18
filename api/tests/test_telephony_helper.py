"""Tests for shared telephony webhook request parsing."""

from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from api.utils.telephony_helper import parse_webhook_request


def _request(
    *,
    method: str = "GET",
    query_string: bytes = b"",
    body: bytes = b"",
    content_type: bytes | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if content_type is not None:
        headers.append((b"content-type", content_type))

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "server": ("example.test", 443),
            "path": "/api/v1/telephony/inbound/run",
            "query_string": query_string,
            "headers": headers,
        },
        receive,
    )


@pytest.mark.asyncio
async def test_parse_webhook_request_reads_get_query_params():
    """Exotel Voicebot GET has no form Content-Type; Starlette form() is empty."""
    request = _request(
        method="GET",
        query_string=(
            b"CallSid=call-inbound-1&AccountSid=exotelaccount"
            b"&From=%2B919876543210&To=%2B9180XXXXXXX1&Direction=incoming"
        ),
    )

    webhook_data, raw_body = await parse_webhook_request(request)

    assert webhook_data["CallSid"] == "call-inbound-1"
    assert webhook_data["AccountSid"] == "exotelaccount"
    assert webhook_data["From"] == "+919876543210"
    assert webhook_data["To"] == "+9180XXXXXXX1"
    assert raw_body == ""


@pytest.mark.asyncio
async def test_parse_webhook_request_prefers_json_body():
    request = _request(
        method="POST",
        body=b'{"CallSid":"from-json","AccountSid":"acct"}',
        content_type=b"application/json",
    )

    webhook_data, _ = await parse_webhook_request(request)

    assert webhook_data == {"CallSid": "from-json", "AccountSid": "acct"}


@pytest.mark.asyncio
async def test_parse_webhook_request_reads_form_body():
    form = urlencode({"CallSid": "from-form", "AccountSid": "acct"}).encode()
    request = _request(
        method="POST",
        body=form,
        content_type=b"application/x-www-form-urlencoded",
    )

    webhook_data, _ = await parse_webhook_request(request)

    assert webhook_data["CallSid"] == "from-form"
    assert webhook_data["AccountSid"] == "acct"


@pytest.mark.asyncio
async def test_parse_webhook_request_empty_get_raises():
    request = _request(method="GET")

    with pytest.raises(ValueError, match="Unable to parse webhook data"):
        await parse_webhook_request(request)
