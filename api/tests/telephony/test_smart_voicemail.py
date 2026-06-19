"""Unit tests for the smart-voicemail feature.

Covers the three pure/unit-testable surfaces: config validation, the Vonage
NCCO builders, and the orchestrator's idempotent resolution latch + branch.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, patch

from api.db import db_client as _db_singleton
from api.schemas.telephony_phone_number import SmartVoicemailConfig
from api.services.telephony.providers.vonage.provider import VonageProvider
from api.services.telephony.smart_voicemail import SmartVoicemailOrchestrator


# --------------------------------------------------------------------------
# SmartVoicemailConfig validation
# --------------------------------------------------------------------------


def test_disabled_config_needs_no_forward_number():
    cfg = SmartVoicemailConfig(enabled=False)
    assert cfg.forward_to_number is None


def test_enabled_requires_forward_number():
    with pytest.raises(ValidationError):
        SmartVoicemailConfig(enabled=True)


def test_enabled_normalizes_e164():
    cfg = SmartVoicemailConfig(enabled=True, forward_to_number="+1 (415) 555-1234")
    assert cfg.forward_to_number == "+14155551234"


def test_enabled_rejects_non_e164():
    with pytest.raises(ValidationError):
        SmartVoicemailConfig(enabled=True, forward_to_number="not-a-number")


def test_ring_timeout_bounds():
    with pytest.raises(ValidationError):
        SmartVoicemailConfig(
            enabled=True, forward_to_number="+14155551234", ring_timeout_seconds=2
        )


# --------------------------------------------------------------------------
# Vonage NCCO builders
# --------------------------------------------------------------------------


def test_caller_hold_ncco_with_moh():
    ncco = VonageProvider.build_caller_hold_ncco("sv-1", "https://x/ring.mp3")
    assert ncco == [
        {
            "action": "conversation",
            "name": "sv-1",
            "startOnEnter": False,
            "endOnExit": True,
            "musicOnHoldUrl": ["https://x/ring.mp3"],
        }
    ]


def test_caller_hold_ncco_without_moh_omits_key():
    ncco = VonageProvider.build_caller_hold_ncco("sv-1", None)
    assert "musicOnHoldUrl" not in ncco[0]
    assert ncco[0]["startOnEnter"] is False


def test_join_conference_ncco_starts_conversation():
    ncco = VonageProvider.build_join_conference_ncco("sv-1")
    assert ncco[0]["action"] == "conversation"
    assert ncco[0]["name"] == "sv-1"
    assert ncco[0]["startOnEnter"] is True


def test_screening_and_ai_connect_ncco_are_websocket_connects():
    screen = VonageProvider.build_screening_connect_ncco("wss://x/screen")
    ai = VonageProvider.build_ai_connect_ncco("wss://x/ai")
    for ncco, uri in ((screen, "wss://x/screen"), (ai, "wss://x/ai")):
        assert ncco[0]["action"] == "connect"
        ep = ncco[0]["endpoint"][0]
        assert ep["type"] == "websocket"
        assert ep["uri"] == uri
        assert ep["content-type"] == "audio/l16;rate=16000"


# --------------------------------------------------------------------------
# Orchestrator resolution latch + branching
# --------------------------------------------------------------------------


class _FakeRedis:
    """Minimal async Redis stand-in (decode_responses=True semantics)."""

    def __init__(self):
        self.store = {}

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)


def _provider_with_mocked_io():
    prov = VonageProvider(
        {"application_id": "app", "private_key": "key", "from_numbers": ["+15550000000"]}
    )
    prov.transfer_leg_to_ncco = AsyncMock(return_value={"status": "ok"})
    prov.hangup_leg = AsyncMock(return_value={"status": "ok"})
    return prov


async def _seed_state(orch, run_id=1):
    await orch._store_state(
        run_id,
        {
            "organization_id": 11,
            "telephony_configuration_id": 22,
            "workflow_id": 33,
            "user_id": 44,
            "workflow_run_id": run_id,
            "caller_leg_uuid": "caller-leg",
            "conference_name": f"sv-{run_id}",
            "screening_leg_uuid": "screen-leg",
            "forward_to_number": "+14155551234",
            "ai_ws_url": "wss://x/api/v1/telephony/ws/33/44/1",
        },
    )


async def test_human_result_bridges_into_conference():
    orch = SmartVoicemailOrchestrator(redis_client=_FakeRedis())
    await _seed_state(orch)
    prov = _provider_with_mocked_io()

    with patch(
        "api.services.telephony.factory.get_telephony_provider_by_id",
        AsyncMock(return_value=prov),
    ), patch.object(_db_singleton, "update_workflow_run", AsyncMock()):
        await orch.on_screening_result(1, "human")

    prov.transfer_leg_to_ncco.assert_awaited_once()
    args = prov.transfer_leg_to_ncco.await_args.args
    assert args[0] == "screen-leg"
    assert args[1] == VonageProvider.build_join_conference_ncco("sv-1")
    prov.hangup_leg.assert_not_awaited()


async def test_voicemail_result_hangs_up_human_and_hands_caller_to_ai():
    orch = SmartVoicemailOrchestrator(redis_client=_FakeRedis())
    await _seed_state(orch)
    prov = _provider_with_mocked_io()

    with patch(
        "api.services.telephony.factory.get_telephony_provider_by_id",
        AsyncMock(return_value=prov),
    ):
        await orch.on_screening_result(1, "voicemail")

    prov.hangup_leg.assert_awaited_once_with("screen-leg")
    prov.transfer_leg_to_ncco.assert_awaited_once()
    args = prov.transfer_leg_to_ncco.await_args.args
    assert args[0] == "caller-leg"
    assert args[1][0]["action"] == "connect"
    assert args[1][0]["endpoint"][0]["uri"] == "wss://x/api/v1/telephony/ws/33/44/1"


async def _run_start(orch, detection):
    prov = _provider_with_mocked_io()
    prov.place_call_with_ncco = AsyncMock(return_value={"uuid": "screen-1"})
    nd = SimpleNamespace(call_id="caller-1", from_number="+17373338910")
    ncco = await orch.start(
        provider=prov,
        smart_voicemail_config={
            "forward_to_number": "+15129701653",
            "ring_timeout_seconds": 20,
            "detection": detection,
        },
        normalized_data=nd,
        organization_id=1,
        telephony_configuration_id=3,
        workflow_id=5,
        user_id=1,
        workflow_run_id=99,
        backend_endpoint="https://x",
        wss_backend_endpoint="wss://x",
    )
    return prov, ncco


async def test_start_dials_from_our_did_not_caller():
    orch = SmartVoicemailOrchestrator(redis_client=_FakeRedis())
    prov, ncco = await _run_start(orch, "vonage")
    kw = prov.place_call_with_ncco.await_args.kwargs
    # Must dial from our owned DID, never the original caller's number.
    assert kw["from_number"] == prov.from_numbers[0]
    assert kw["from_number"] != "+17373338910"
    assert kw["to_number"] == "+15129701653"
    # Caller is parked in the ringback/hold conference.
    assert ncco[0]["action"] == "conversation"


async def test_start_vonage_detection_uses_amd_and_hold_conf():
    orch = SmartVoicemailOrchestrator(redis_client=_FakeRedis())
    prov, _ = await _run_start(orch, "vonage")
    kw = prov.place_call_with_ncco.await_args.kwargs
    assert kw["advanced_machine_detection"] == {
        "behavior": "continue",
        "mode": "detect",
        "beep_timeout": 45,
    }
    # Human leg holds in a silent conference (not our websocket).
    assert kw["ncco"][0]["action"] == "conversation"


async def test_start_own_detection_connects_websocket_no_amd():
    orch = SmartVoicemailOrchestrator(redis_client=_FakeRedis())
    prov, _ = await _run_start(orch, "own")
    kw = prov.place_call_with_ncco.await_args.kwargs
    assert kw["advanced_machine_detection"] is None
    assert kw["ncco"][0]["action"] == "connect"
    assert kw["ncco"][0]["endpoint"][0]["type"] == "websocket"


def test_human_hold_ncco_is_silent_conference():
    ncco = VonageProvider.build_human_hold_ncco("svhold-1")
    assert ncco[0]["action"] == "conversation"
    assert ncco[0]["name"] == "svhold-1"
    assert "musicOnHoldUrl" not in ncco[0]


def test_detection_defaults_to_vonage():
    cfg = SmartVoicemailConfig(enabled=True, forward_to_number="+14155551234")
    assert cfg.detection == "vonage"


async def test_latch_makes_first_result_win():
    fake = _FakeRedis()
    orch = SmartVoicemailOrchestrator(redis_client=fake)
    await _seed_state(orch)
    prov = _provider_with_mocked_io()

    with patch(
        "api.services.telephony.factory.get_telephony_provider_by_id",
        AsyncMock(return_value=prov),
    ), patch.object(_db_singleton, "update_workflow_run", AsyncMock()):
        await orch.on_screening_result(1, "human")
        # Late no-answer event must be ignored.
        await orch.on_screening_result(1, "no_answer")

    # Only the first (human) resolution acted.
    prov.transfer_leg_to_ncco.assert_awaited_once()
    prov.hangup_leg.assert_not_awaited()
    assert fake.store["sv:resolved:1"] == "human"
