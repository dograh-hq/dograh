"""Tool settings reach both-leg playback only after successful preparation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.telephony.transfer_event_protocol import (
    TransferEvent,
    TransferEventType,
)
from api.services.workflow import pipecat_engine_custom_tools as tools
from api.services.workflow.tools.transfer_resolver import ResolvedTransferConfig
from api.tests.test_transfer_message_playback import RecordingEngine, TransferToolModel


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,supported,audio_url,completion",
    [
        (True, True, "https://api.example.com/clip", "answered"),
        (True, True, None, "answered"),
        (False, True, None, "answered"),
        (True, False, None, "answered"),
        (True, True, "https://api.example.com/clip", "timeout"),
        (True, True, "https://api.example.com/clip", "answer_at_timeout"),
    ],
)
async def test_introduction_preparation_and_dial_order(
    monkeypatch, enabled, supported, audio_url, completion
):
    engine = RecordingEngine()
    tool = TransferToolModel()
    tool.definition["config"]["introduction_enabled"] = enabled
    manager = tools.CustomToolManager(engine)
    outcome = TransferEvent(
        type=TransferEventType.TRANSFER_FAILED
        if completion == "timeout"
        else TransferEventType.DESTINATION_ANSWERED,
        transfer_id="tx-1",
        original_call_sid="caller",
        reason="timeout" if completion == "timeout" else None,
    )
    transfer_manager = SimpleNamespace(
        store_transfer_context=AsyncMock(),
        publish_transfer_event=AsyncMock(return_value=outcome),
        wait_for_transfer_completion=AsyncMock(
            return_value=outcome if completion == "answered" else None
        ),
    )
    run = SimpleNamespace(
        mode="twilio", initial_context={}, gathered_context={"call_id": "caller"}
    )
    hold_running = asyncio.Event()

    async def hold(stop_event, **kwargs):
        hold_running.set()
        await stop_event.wait()
        hold_running.clear()

    async def prepare(*args):
        await hold_running.wait()
        engine.events.append("prepare")
        return audio_url

    async def dial(**kwargs):
        engine.events.append(("dial", kwargs))
        return {"call_sid": "destination"}

    provider = SimpleNamespace(
        supports_transfers=lambda: True,
        validate_config=lambda: True,
        supports_transfer_introduction=lambda: supported,
        transfer_call=dial,
        end_transfer_leg=AsyncMock(),
    )
    preparation = AsyncMock(side_effect=prepare)
    monkeypatch.setattr(
        tools.db_client, "get_workflow_run_by_id", AsyncMock(return_value=run)
    )
    monkeypatch.setattr(
        tools, "get_telephony_provider_for_run", AsyncMock(return_value=provider)
    )
    monkeypatch.setattr(
        tools, "get_call_transfer_manager", AsyncMock(return_value=transfer_manager)
    )
    monkeypatch.setattr(
        tools,
        "resolve_transfer_config",
        AsyncMock(
            return_value=ResolvedTransferConfig(
                destination="+15555550123", timeout_seconds=30, source="static"
            )
        ),
    )
    monkeypatch.setattr(tools, "prepare_transfer_introduction", preparation)
    monkeypatch.setattr(tools, "play_audio_loop", hold)
    callback = AsyncMock()
    await asyncio.wait_for(
        manager._create_transfer_call_handler(tool, "transfer_call")(
            SimpleNamespace(arguments={}, result_callback=callback)
        ),
        timeout=2,
    )
    dial_kwargs = next(
        event[1]
        for event in engine.events
        if isinstance(event, tuple) and event[0] == "dial"
    )
    if enabled and supported:
        assert engine.events.index("wait_for_playback") < engine.events.index("prepare")
        assert engine.events.index("prepare") < engine.events.index(
            ("dial", dial_kwargs)
        )
    else:
        preparation.assert_not_called()
    if audio_url:
        assert dial_kwargs["introduction_audio_url"] == audio_url
        context = transfer_manager.store_transfer_context.call_args.args[0]
        assert context.introduction_audio_url == audio_url
        assert context.conference_name == "transfer-" + context.transfer_id
    else:
        assert "introduction_audio_url" not in dial_kwargs
    if completion != "answered":
        assert transfer_manager.publish_transfer_event.call_args.kwargs == {
            "only_if_pending": True
        }
    if completion == "timeout":
        provider.end_transfer_leg.assert_awaited_once_with("destination")
        assert callback.call_args.args[0]["status"] == "transfer_failed"
        assert callback.call_args.args[0]["reason"] == "timeout"
    else:
        provider.end_transfer_leg.assert_not_awaited()
        assert callback.call_args.args[0]["status"] == "transfer_success"
    assert not hold_running.is_set()
