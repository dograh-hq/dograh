"""Unit tests for the VoxPro telephony provider (mocked HTTP, no live carrier)."""

import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from api.services.telephony.providers.voxpro.provider import VoxProProvider


def _provider(**over):
    cfg = {
        "api_key": "vpk_test_123",
        "tenant_id": "AI_Katha_1783948668",
        "api_base": "https://connector.voxprosolutions.com",
        "from_numbers": ["08071661528"],
    }
    cfg.update(over)
    return VoxProProvider(cfg)


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        return self._resp

    def get(self, url, headers=None):
        self.calls.append(("GET", url, None, headers))
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def test_validate_config():
    assert _provider().validate_config() is True
    assert _provider(api_key="").validate_config() is False
    assert _provider(from_numbers=[]).validate_config() is False


def test_supports_transfers():
    assert _provider().supports_transfers() is True


def test_get_available_phone_numbers():
    assert _provider().get_available_phone_numbers.__name__  # coroutine fn exists


@pytest.mark.asyncio
async def test_initiate_call_posts_to_connector():
    resp = _FakeResp(201, {"call_id": "sess-abc", "session_uuid": "sess-abc", "state": "ringing"})
    session = _FakeSession(resp)

    with patch("api.services.telephony.providers.voxpro.provider.aiohttp.ClientSession",
               return_value=session), \
         patch("api.services.telephony.providers.voxpro.provider.get_backend_endpoints",
               new=AsyncMock(return_value=("https://api.dograh.example", "wss://api.dograh.example"))):
        result = await _provider().initiate_call(
            to_number="+919513049206", webhook_url="https://api.dograh.example/status",
            workflow_run_id=42,
        )

    assert result.call_id == "sess-abc"
    assert result.caller_number == "08071661528"
    method, url, body, headers = session.calls[0]
    assert url.endswith("/v1/calls/originate")
    assert headers["X-API-Key"] == "vpk_test_123"
    assert body["to_number"] == "919513049206"           # + stripped
    assert body["ws_url"].startswith("wss://api.dograh.example")
    assert body["workflow_run_id"] == "42"


def test_parse_inbound_webhook():
    data = {"call_id": "c1", "from": "+919999999999", "to": "08071661528",
            "status": "ringing", "tenant_id": "AI_Katha_1783948668"}
    n = VoxProProvider.parse_inbound_webhook(data)
    assert n.provider == "voxpro"
    assert n.direction == "inbound"
    assert n.account_id == "AI_Katha_1783948668"
    assert n.call_id == "c1"


def test_validate_account_id():
    cfg = {"tenant_id": "AI_Katha_1783948668"}
    assert VoxProProvider.validate_account_id(cfg, "AI_Katha_1783948668") is True
    assert VoxProProvider.validate_account_id(cfg, "someone_else") is False
    assert VoxProProvider.validate_account_id(cfg, "") is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_hmac():
    p = _provider()
    body = json.dumps({"call_id": "c1"}, separators=(",", ":"))
    good = hmac.new(b"vpk_test_123", body.encode(), hashlib.sha256).hexdigest()

    assert await p.verify_inbound_signature("u", {"call_id": "c1"},
                                            {"X-VoxPro-Signature": good}, body=body) is True
    assert await p.verify_inbound_signature("u", {"call_id": "c1"},
                                            {"X-VoxPro-Signature": "deadbeef"}, body=body) is False
    assert await p.verify_inbound_signature("u", {"call_id": "c1"}, {}, body=body) is False
