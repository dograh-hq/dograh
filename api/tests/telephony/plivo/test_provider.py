import json
from unittest.mock import AsyncMock, patch

import pytest

from api.services.telephony.providers.plivo.provider import PlivoProvider


class _Response:
    status = 201

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def text(self):
        return json.dumps({"request_uuid": "destination-request-uuid"})


class _Session:
    def __init__(self):
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def post(self, endpoint, *, json, auth):
        self.post_calls.append((endpoint, json, auth))
        return _Response()


@pytest.mark.asyncio
async def test_transfer_call_dials_destination_with_answer_and_hangup_urls():
    provider = PlivoProvider(
        {
            "auth_id": "MA123",
            "auth_token": "secret",
            "from_numbers": ["+15551230001"],
        }
    )
    session = _Session()

    with (
        patch(
            "api.services.telephony.providers.plivo.provider.get_backend_endpoints",
            new=AsyncMock(return_value=("https://backend.test", "wss://backend.test")),
        ),
        patch(
            "api.services.telephony.providers.plivo.provider.aiohttp.ClientSession",
            return_value=session,
        ),
    ):
        result = await provider.transfer_call(
            destination="+15551230002",
            transfer_id="transfer-1",
            conference_name="conference-1",
            timeout=20,
        )

    payload = session.post_calls[0][1]
    assert payload == {
        "to": "+15551230002",
        "from": "15551230001",
        "answer_url": "https://backend.test/api/v1/telephony/plivo/transfer-xml/conference-1/transfer-1",
        "answer_method": "POST",
        "hangup_url": "https://backend.test/api/v1/telephony/plivo/transfer-result/transfer-1",
        "hangup_method": "POST",
        "ring_timeout": 20,
    }
    assert result["call_sid"] == "destination-request-uuid"
