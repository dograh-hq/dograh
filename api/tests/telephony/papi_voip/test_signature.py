"""Tests for Webhook and Inbound Signature verification."""

import hashlib
import hmac
import json
import pytest

from api.services.telephony.providers.papi_voip.provider import PapiVoipProvider


@pytest.mark.asyncio
async def test_webhook_signature_hmac_valid():
    secret = "my-secret-key"
    provider = PapiVoipProvider(
        {
            "api_key": "api-key-123",
            "instance_id": "inst-999",
            "webhook_secret": secret,
        }
    )

    payload = {"status": "completed", "call_id": "call-123", "duration": 45}
    raw_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    is_valid = await provider.verify_webhook_signature(
        "https://domain.com/webhook",
        payload,
        signature=sig,
    )
    assert is_valid is True


@pytest.mark.asyncio
async def test_webhook_signature_hmac_invalid():
    provider = PapiVoipProvider(
        {
            "api_key": "api-key-123",
            "instance_id": "inst-999",
            "webhook_secret": "my-secret-key",
        }
    )

    payload = {"status": "completed", "call_id": "call-123"}
    is_valid = await provider.verify_webhook_signature(
        "https://domain.com/webhook",
        payload,
        signature="invalid-fake-signature",
    )
    assert is_valid is False


@pytest.mark.asyncio
async def test_inbound_signature_header_token():
    provider = PapiVoipProvider(
        {
            "api_key": "papi_live_token_777",
            "instance_id": "inst-999",
        }
    )

    headers = {"x-api-key": "papi_live_token_777"}
    is_valid = await provider.verify_inbound_signature(
        "https://domain.com/inbound",
        {"event": "call.received", "from": "5511999999999"},
        headers=headers,
    )
    assert is_valid is True


@pytest.mark.asyncio
async def test_inbound_signature_instance_id_fallback():
    provider = PapiVoipProvider(
        {
            "api_key": "papi_live_token_777",
            "instance_id": "inst-999",
        }
    )

    headers = {}
    is_valid = await provider.verify_inbound_signature(
        "https://domain.com/inbound",
        {"instance_id": "inst-999", "from": "5511999999999"},
        headers=headers,
    )
    assert is_valid is True
