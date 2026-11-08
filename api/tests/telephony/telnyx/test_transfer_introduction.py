from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.telephony.providers.telnyx import introduction, routes, strategies
from api.services.telephony.providers.telnyx.provider import TelnyxProvider
from api.services.telephony.transfer_event_protocol import (
    TransferContext,
    TransferEvent,
    TransferEventType,
)

AUDIO_URL = "https://backend.example/api/v1/telephony/twilio/transfer-audio/token"


class _FakePipeline:
    def __init__(self, sets):
        self._sets, self._ops = sets, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def sadd(self, key, member):
        self._ops.append(("sadd", key, member))
        return self

    def scard(self, key):
        self._ops.append(("scard", key))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key))
        return self

    async def execute(self):
        results = []
        for op, key, *rest in self._ops:
            members = self._sets.setdefault(key, set())
            if op == "sadd":
                results.append(0 if rest[0] in members else 1)
                members.add(rest[0])
            elif op == "scard":
                results.append(len(members))
            else:
                results.append(True)
        return results


class _FakeRedis:
    def __init__(self):
        self.sets = {}

    def pipeline(self, transaction=True):
        return _FakePipeline(self.sets)


def _manager(result=None):
    redis = _FakeRedis()
    return SimpleNamespace(
        _get_redis=AsyncMock(return_value=redis),
        store_transfer_context=AsyncMock(),
        remove_transfer_context=AsyncMock(),
        publish_transfer_event=AsyncMock(
            side_effect=lambda event, **_: result or event
        ),
        get_transfer_result=AsyncMock(return_value=result),
    )


def _transfer(**overrides):
    values = dict(
        transfer_id="t-1",
        call_sid="dest-leg",
        target_number="+15551230003",
        tool_uuid="tool",
        original_call_sid="caller-leg",
        conference_name="transfer-t-1",
        initiated_at=0.0,
        workflow_run_id=7,
        introduction_audio_url=AUDIO_URL,
    )
    values.update(overrides)
    return TransferContext(**values)


def _provider():
    return SimpleNamespace(
        api_key="placeholder-api-key",
        create_conference=AsyncMock(return_value="conf-1"),
        end_transfer_leg=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_bridge_happens_once_after_both_legs_finish():
    manager, provider, transfer = _manager(), _provider(), _transfer()
    join = AsyncMock(return_value=True)
    with patch.object(strategies.TelnyxConferenceStrategy, "_join_caller", join):
        await introduction.leg_finished(manager, transfer, provider, "destination")
        provider.create_conference.assert_not_awaited()

        # A retried destination webhook is not the second leg.
        await introduction.leg_finished(manager, transfer, provider, "destination")
        provider.create_conference.assert_not_awaited()

        await introduction.leg_finished(manager, transfer, provider, "caller")
        await introduction.leg_finished(manager, transfer, provider, "caller")

    provider.create_conference.assert_awaited_once_with(
        seed_call_control_id="dest-leg", name="transfer-t-1"
    )
    assert join.await_args.kwargs["caller_call_control_id"] == "caller-leg"
    assert transfer.conference_id == "conf-1"
    provider.end_transfer_leg.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_bridge_hangs_up_both_legs():
    manager, provider, transfer = _manager(), _provider(), _transfer()
    provider.create_conference.return_value = None
    await introduction.leg_finished(manager, transfer, provider, "destination")
    await introduction.leg_finished(manager, transfer, provider, "caller")

    ended = [call.args[0] for call in provider.end_transfer_leg.await_args_list]
    assert ended == ["dest-leg", "caller-leg"]
    manager.remove_transfer_context.assert_awaited_once_with("t-1")


@pytest.mark.asyncio
async def test_destination_answer_announces_then_plays_without_seeding_conference():
    manager, provider, transfer = _manager(), _provider(), _transfer(call_sid="dial-id")
    play = AsyncMock(return_value=True)
    with (
        patch.object(
            routes, "_resolve_telnyx_provider", AsyncMock(return_value=provider)
        ),
        patch.object(routes, "play_introduction", play),
    ):
        result = await routes._handle_introduction_destination_event(
            manager, transfer, "call.answered", "live-dest-leg"
        )

    assert result == {"status": "success"}
    event = manager.publish_transfer_event.await_args.args[0]
    assert event.type == TransferEventType.DESTINATION_ANSWERED
    play.assert_awaited_once_with("placeholder-api-key", "live-dest-leg", AUDIO_URL)
    assert transfer.call_sid == "live-dest-leg"
    provider.create_conference.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_answer_after_failure_ends_destination_without_playing():
    failed = TransferEvent(
        type=TransferEventType.TRANSFER_FAILED,
        transfer_id="t-1",
        original_call_sid="caller-leg",
        reason="timeout",
    )
    manager, provider, transfer = _manager(result=failed), _provider(), _transfer()
    play = AsyncMock()
    with (
        patch.object(
            routes, "_resolve_telnyx_provider", AsyncMock(return_value=provider)
        ),
        patch.object(routes, "play_introduction", play),
    ):
        await routes._handle_introduction_destination_event(
            manager, transfer, "call.answered", "live-dest-leg"
        )

    provider.end_transfer_leg.assert_awaited_once_with("live-dest-leg")
    play.assert_not_awaited()


@pytest.mark.asyncio
async def test_destination_hangup_during_introduction_hangs_up_caller():
    answered = TransferEvent(
        type=TransferEventType.DESTINATION_ANSWERED,
        transfer_id="t-1",
        original_call_sid="caller-leg",
    )
    manager, transfer = _manager(result=answered), _transfer()
    hangup = AsyncMock()
    with patch.object(routes, "_hangup_caller_leg", hangup):
        result = await routes._handle_introduction_destination_event(
            manager, transfer, "call.hangup", "dest-leg"
        )

    assert result == {"status": "success"}
    hangup.assert_awaited_once_with(transfer)


@pytest.mark.asyncio
async def test_pre_answer_hangup_falls_through_to_plain_failure():
    manager = _manager(result=None)
    result = await routes._handle_introduction_destination_event(
        manager, _transfer(), "call.hangup", "dest-leg"
    )
    assert result is None


@pytest.mark.asyncio
async def test_caller_hangup_before_bridge_releases_destination():
    manager, provider, transfer = _manager(), _provider(), _transfer()
    manager.find_transfer_context_for_call = AsyncMock(return_value=transfer)
    with patch.object(
        routes, "get_call_transfer_manager", AsyncMock(return_value=manager)
    ):
        await routes._handle_introduction_caller_event(
            "call.hangup", "caller-leg", provider
        )

    event = manager.publish_transfer_event.await_args
    assert event.args[0].reason == "caller_hangup"
    assert event.kwargs == {"only_if_pending": True}
    provider.end_transfer_leg.assert_awaited_once_with("dest-leg")


@pytest.mark.asyncio
async def test_strategy_plays_introduction_to_caller_instead_of_joining():
    strategy = strategies.TelnyxConferenceStrategy()
    play = AsyncMock(return_value=True)
    with (
        patch.object(
            strategy,
            "_find_transfer_context_for_call",
            AsyncMock(return_value=_transfer()),
        ),
        patch.object(strategy, "_join_caller", AsyncMock()) as join,
        patch.object(introduction, "play_introduction", play),
    ):
        joined = await strategy.execute_transfer(
            {"call_control_id": "caller-leg", "api_key": "placeholder-api-key"}
        )

    assert joined is True
    play.assert_awaited_once_with("placeholder-api-key", "caller-leg", AUDIO_URL)
    join.assert_not_awaited()


@pytest.mark.asyncio
async def test_dial_payload_never_carries_the_introduction_url():
    provider = TelnyxProvider(
        {
            "api_key": "placeholder-api-key",
            "connection_id": "connection-id",
            "from_numbers": ["+15551230002"],
        }
    )
    sent = {}

    class _Response:
        status = 200

        async def text(self):
            return '{"data": {"call_control_id": "dest-leg"}}'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json, headers):
            sent.update(json)
            return _Response()

    with (
        patch(
            "api.services.telephony.providers.telnyx.provider.get_backend_endpoints",
            AsyncMock(return_value=("https://backend.example", "")),
        ),
        patch(
            "api.services.telephony.providers.telnyx.provider.aiohttp.ClientSession",
            return_value=_Session(),
        ),
    ):
        await provider.transfer_call(
            "+15551230003", "t-1", "transfer-t-1", introduction_audio_url=AUDIO_URL
        )

    assert "introduction_audio_url" not in sent
    assert provider.supports_transfer_introduction()
