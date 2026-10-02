"""Provider playback, media capabilities, and signed introduction webhooks."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree.ElementTree import fromstring

import httpx
import pytest
from fastapi import FastAPI
from twilio.request_validator import RequestValidator

from api.services.telephony import transfer_audio
from api.services.telephony.call_transfer_manager import CallTransferManager
from api.services.telephony.providers.twilio import provider as provider_module
from api.services.telephony.providers.twilio import routes, strategies
from api.services.telephony.providers.twilio.introduction import introduction_urls
from api.services.telephony.providers.twilio.provider import TwilioProvider
from api.services.telephony.transfer_event_protocol import (
    TransferContext,
    TransferEvent,
    TransferEventType,
)


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.published = []

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    async def publish(self, channel, data):
        self.published.append((channel, json.loads(data)))

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def pubsub(self):
        return SimpleNamespace(
            subscribe=AsyncMock(), unsubscribe=AsyncMock(), close=AsyncMock()
        )


@pytest.fixture
async def transfer(monkeypatch):
    redis = MemoryRedis()
    manager = CallTransferManager(redis_client=redis)
    context = TransferContext(
        transfer_id="tx-1",
        call_sid="CA-destination",
        target_number="+15555550123",
        tool_uuid="tool-1",
        original_call_sid="CA-caller",
        conference_name="transfer-1&2",
        initiated_at=0,
        workflow_run_id=42,
        introduction_audio_url="https://example.com/audio?token=secret&expires=300",
    )
    await manager.store_transfer_context(context)
    provider = TwilioProvider(
        {
            "account_sid": "AC-test",
            "auth_token": "test-secret",
            "from_numbers": ["+15555550111"],
        }
    )
    provider.end_transfer_leg = AsyncMock()
    monkeypatch.setattr(
        routes, "get_call_transfer_manager", AsyncMock(return_value=manager)
    )
    monkeypatch.setattr(
        transfer_audio, "get_call_transfer_manager", AsyncMock(return_value=manager)
    )
    monkeypatch.setattr(
        transfer_audio,
        "get_backend_endpoints",
        AsyncMock(return_value=("https://api.example.com", "")),
    )
    monkeypatch.setattr(
        routes.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=SimpleNamespace(workflow_id=3)),
    )
    monkeypatch.setattr(
        routes.db_client,
        "get_workflow_by_id",
        AsyncMock(return_value=SimpleNamespace(organization_id=7)),
    )
    provider_lookup = AsyncMock(return_value=provider)
    monkeypatch.setattr(routes, "get_telephony_provider_for_run", provider_lookup)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/telephony")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://api.example.com"
    ) as client:
        yield SimpleNamespace(
            client=client,
            manager=manager,
            redis=redis,
            context=context,
            provider=provider,
            lookup=provider_lookup,
        )


async def signed_post(client, path, data, secret="test-secret"):
    signature = RequestValidator(secret).compute_signature(
        "https://api.example.com" + path, data
    )
    return await client.post(path, data=data, headers={"X-Twilio-Signature": signature})


@pytest.mark.asyncio
async def test_each_leg_plays_the_same_clip_before_joining(transfer):
    for call_sid in ("CA-caller", "CA-destination"):
        response = await signed_post(
            transfer.client,
            "/api/v1/telephony/twilio/transfer-introduction/tx-1",
            {"CallSid": call_sid},
        )
        assert response.status_code == 200
        xml = fromstring(response.text)
        assert [child.tag for child in xml] == ["Play", "Dial"]
        assert xml.find("Play").text == transfer.context.introduction_audio_url
        assert xml.find("Dial/Conference").text == "transfer-1&2"
        assert xml.find("Dial/Conference").get("endConferenceOnExit") == "true"
    assert transfer.lookup.call_args.args[1] == 7


@pytest.mark.asyncio
async def test_playback_failure_fallback_joins_without_audio(transfer):
    response = await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/transfer-introduction/tx-1?skip_audio=true",
        {"CallSid": "CA-caller"},
    )
    assert response.status_code == 200
    assert [child.tag for child in fromstring(response.text)] == ["Dial"]


@pytest.mark.asyncio
async def test_callbacks_require_this_organizations_signature(transfer):
    response = await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/transfer-introduction/tx-1",
        {"CallSid": "CA-caller"},
        secret="another-org-secret",
    )
    assert response.status_code == 401
    response = await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/transfer-status/tx-1",
        {"CallSid": "unrelated-call", "CallStatus": "in-progress"},
    )
    assert response.status_code == 403
    assert not transfer.redis.published


@pytest.mark.asyncio
async def test_answer_is_available_even_before_waiter_subscribes(transfer):
    response = await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/transfer-status/tx-1",
        {"CallSid": "CA-destination", "CallStatus": "in-progress"},
    )
    assert response.status_code == 200
    event = await transfer.manager.wait_for_transfer_completion("tx-1", 0.01)
    assert event.action == "destination_answered"
    assert event.conference_name == transfer.context.conference_name


@pytest.mark.asyncio
async def test_destination_hangup_during_clip_ends_caller_leg(transfer):
    response = await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/transfer-status/tx-1",
        {"CallSid": "CA-destination", "CallStatus": "completed"},
    )
    assert response.status_code == 200
    transfer.provider.end_transfer_leg.assert_awaited_once_with("CA-caller")


@pytest.mark.asyncio
async def test_late_answer_does_not_overwrite_terminal_hangup(transfer):
    path = "/api/v1/telephony/twilio/transfer-status/tx-1"
    await signed_post(
        transfer.client, path, {"CallSid": "CA-destination", "CallStatus": "completed"}
    )
    await signed_post(
        transfer.client,
        path,
        {"CallSid": "CA-destination", "CallStatus": "in-progress"},
    )
    event = await transfer.manager.wait_for_transfer_completion("tx-1", 0.01)
    assert event.action == "transfer_failed"


@pytest.mark.asyncio
async def test_caller_hangup_during_clip_ends_destination_leg(transfer, monkeypatch):
    monkeypatch.setattr(routes, "_process_status_update", AsyncMock())
    response = await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/status-callback/42",
        {"CallSid": "CA-caller", "CallStatus": "completed"},
    )
    assert response.status_code == 200
    transfer.provider.end_transfer_leg.assert_awaited_once_with("CA-destination")
    assert (
        await transfer.manager.get_transfer_result("tx-1")
    ).reason == "caller_hangup"


@pytest.mark.asyncio
async def test_caller_callback_cannot_cancel_another_runs_transfer(
    transfer, monkeypatch
):
    monkeypatch.setattr(routes, "_process_status_update", AsyncMock())
    response = await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/status-callback/99",
        {"CallSid": "CA-caller", "CallStatus": "completed"},
    )
    assert response.status_code == 200
    transfer.provider.end_transfer_leg.assert_not_awaited()
    assert await transfer.manager.get_transfer_result("tx-1") is None


@pytest.mark.asyncio
async def test_caller_hangup_before_dial_response_prevents_late_join(
    transfer, monkeypatch
):
    monkeypatch.setattr(routes, "_process_status_update", AsyncMock())
    transfer.context.call_sid = None
    await transfer.manager.store_transfer_context(transfer.context)
    await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/status-callback/42",
        {"CallSid": "CA-caller", "CallStatus": "completed"},
    )
    response = await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/transfer-introduction/tx-1",
        {"CallSid": "CA-destination"},
    )
    assert [child.tag for child in fromstring(response.text)] == ["Hangup"]
    await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/transfer-status/tx-1",
        {"CallSid": "CA-destination", "CallStatus": "in-progress"},
    )
    transfer.provider.end_transfer_leg.assert_awaited_once_with("CA-destination")


@pytest.mark.asyncio
async def test_local_timeout_callback_leaves_caller_with_agent(transfer):
    await transfer.manager.publish_transfer_event(
        TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id="tx-1",
            original_call_sid="CA-caller",
            reason="timeout",
        )
    )
    response = await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/transfer-status/tx-1",
        {"CallSid": "CA-destination", "CallStatus": "completed"},
    )
    assert response.status_code == 200
    transfer.provider.end_transfer_leg.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["busy", "no-answer", "failed", "canceled"])
async def test_unanswered_destination_preserves_caller(transfer, status):
    await signed_post(
        transfer.client,
        "/api/v1/telephony/twilio/transfer-status/tx-1",
        {"CallSid": "CA-destination", "CallStatus": status},
    )
    transfer.provider.end_transfer_leg.assert_not_awaited()
    assert (await transfer.manager.get_transfer_result("tx-1")).reason == status


@pytest.mark.asyncio
async def test_audio_capability_is_scoped_and_expires(transfer):
    url = await transfer_audio.store_transfer_audio(b"RIFF-test-audio")
    response = await transfer.client.get(url)
    assert response.content == b"RIFF-test-audio"
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-store"
    other_url = await transfer_audio.store_transfer_audio(b"other-call-audio")
    assert other_url != url
    assert (await transfer.client.get(other_url)).content == b"other-call-audio"
    audio_keys = [
        key for key in transfer.redis.values if key.startswith("transfer:audio:")
    ]
    assert all(transfer.redis.ttls[key] == 300 for key in audio_keys)
    await transfer.redis.delete(*audio_keys)
    assert (await transfer.client.get(url)).status_code == 404
    assert (
        await transfer.client.get("/api/v1/telephony/twilio/transfer-audio/guess")
    ).status_code == 404


def test_both_provider_paths_have_audio_failure_fallback():
    urls = introduction_urls("https://api.example.com", "tx-1")
    assert urls["FallbackUrl"] == urls["Url"] + "?skip_audio=true"
    assert urls["Method"] == urls["FallbackMethod"] == "POST"


class FakeSession:
    def __init__(self, posts):
        self.posts = posts
        self.status = 201

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self

    async def text(self):
        return "{}"

    async def json(self):
        return {"sid": "CA-destination", "status": "queued"}


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_dial_uses_playback_urls_only_when_introduction_is_ready(
    transfer, monkeypatch, enabled
):
    posts = []
    monkeypatch.setattr(
        provider_module.aiohttp, "ClientSession", lambda **kwargs: FakeSession(posts)
    )
    monkeypatch.setattr(
        provider_module,
        "get_backend_endpoints",
        AsyncMock(return_value=("https://api.example.com", "")),
    )
    await transfer.provider.transfer_call(
        destination="+15555550123",
        transfer_id="tx-1",
        conference_name="conference",
        introduction_audio_url=transfer.context.introduction_audio_url
        if enabled
        else None,
    )
    data = posts[0][1]["data"]
    assert "introduction_audio_url" not in data
    if enabled:
        assert "Twiml" not in data
        assert data["FallbackUrl"] == data["Url"] + "?skip_audio=true"
        assert "/twilio/transfer-status/tx-1" in data["StatusCallback"]
        assert data["StatusCallbackEvent"] == ["answered", "completed"]
    else:
        assert fromstring(data["Twiml"]).find("Say") is not None


@pytest.mark.asyncio
async def test_caller_redirect_preserves_context_for_twiml_and_fallback(
    transfer, monkeypatch
):
    posts = []
    session = FakeSession(posts)
    session.status = 200
    monkeypatch.setattr(strategies.aiohttp, "ClientSession", lambda **kwargs: session)
    monkeypatch.setattr(
        strategies,
        "get_backend_endpoints",
        AsyncMock(return_value=("https://api.example.com", "")),
    )
    strategy = strategies.TwilioConferenceStrategy()
    monkeypatch.setattr(
        strategy,
        "_find_transfer_context_for_call",
        AsyncMock(return_value=transfer.context),
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(strategy, "_cleanup_transfer_context", cleanup)
    assert await strategy.execute_transfer(
        {"call_sid": "CA-caller", "account_sid": "AC-test", "auth_token": "test-secret"}
    )
    assert posts[0][1]["data"] == introduction_urls("https://api.example.com", "tx-1")
    cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_failed_caller_redirect_cancels_destination(transfer, monkeypatch):
    session = FakeSession([])
    session.status = 404
    monkeypatch.setattr(strategies.aiohttp, "ClientSession", lambda **kwargs: session)
    monkeypatch.setattr(
        strategies,
        "get_backend_endpoints",
        AsyncMock(return_value=("https://api.example.com", "")),
    )
    monkeypatch.setattr(
        "api.services.telephony.call_transfer_manager.get_call_transfer_manager",
        AsyncMock(return_value=transfer.manager),
    )
    hangup = AsyncMock(return_value=True)
    monkeypatch.setattr(strategies.TwilioHangupStrategy, "execute_hangup", hangup)
    strategy = strategies.TwilioConferenceStrategy()
    monkeypatch.setattr(
        strategy,
        "_find_transfer_context_for_call",
        AsyncMock(return_value=transfer.context),
    )
    assert not await strategy.execute_transfer(
        {"call_sid": "CA-caller", "account_sid": "AC-test", "auth_token": "test-secret"}
    )
    assert hangup.call_args.args[0]["call_sid"] == "CA-destination"
    assert (
        await transfer.manager.get_transfer_result("tx-1")
    ).reason == "caller_redirect_failed"
