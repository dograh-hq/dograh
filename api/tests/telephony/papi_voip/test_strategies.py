from unittest.mock import patch

import pytest

from api.services.telephony.providers.papi_voip.strategies import PapiVoipHangupStrategy


class _Response:
    def __init__(self, status, payload=None):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self.payload

    async def text(self):
        return str(self.payload)


class _Session:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, endpoint, **kwargs):
        self.calls.append(("GET", endpoint, kwargs))
        return _Response(200, {"calls": [{"sid": "papi-call-123"}]})

    def delete(self, endpoint, **kwargs):
        self.calls.append(("DELETE", endpoint, kwargs))
        return _Response(204)


@pytest.mark.asyncio
async def test_hangup_deletes_specified_call_id():
    session = _Session()

    with patch(
        "api.services.telephony.providers.papi_voip.strategies.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await PapiVoipHangupStrategy().execute_hangup(
            {
                "call_id": "call-123",
                "base_url": "https://api.papi.api.br",
                "api_key": "instance-api-key",
                "instance_id": "instance-123",
            }
        )

    assert result is True
    assert session.calls == [
        (
            "DELETE",
            "https://api.papi.api.br/api/instances/instance-123/voice/calls/call-123",
            {"headers": {"x-api-key": "instance-api-key", "apikey": "instance-api-key"}},
        ),
    ]


@pytest.mark.asyncio
async def test_hangup_rejects_active_sentinel_or_missing_fields():
    result = await PapiVoipHangupStrategy().execute_hangup(
        {
            "call_id": "active",
            "base_url": "https://api.papi.api.br",
            "api_key": "instance-api-key",
            "instance_id": "instance-123",
        }
    )
    assert result is False
