"""Tests for the pre-transfer message playing before the call is handed off.

An external PBX (e.g. VICIdial) pulls the customer off the Dograh leg as soon
as its transfer API returns, so queueing the "transferring you now" message and
immediately calling that API means the caller never hears it. These tests cover
the speech-playback tracking primitive on the engine and the ordering it
enforces in the transfer tool handler.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TTSSpeakFrame,
)
from pipecat.utils.enums import EndTaskReason

from api.enums import ToolCategory, WorkflowRunMode
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
from api.services.workflow.tools.transfer_resolver import ResolvedTransferConfig


def make_engine() -> PipecatEngine:
    """An engine with just enough state for the playback tracking helpers."""
    return PipecatEngine(workflow=None, call_context_vars={}, workflow_run_id=1)


class TestSpeechPlaybackTracking:
    """Tests for arm_speech_playback / wait_for_speech_playback."""

    @pytest.mark.asyncio
    async def test_wait_returns_after_speech_starts_and_stops(self):
        engine = make_engine()
        engine.arm_speech_playback()

        waiter = asyncio.create_task(engine.wait_for_speech_playback())
        await asyncio.sleep(0)
        assert not waiter.done()

        await engine.should_mute_user(BotStartedSpeakingFrame())
        await asyncio.sleep(0)
        assert not waiter.done(), "wait must not return while the bot is speaking"

        await engine.should_mute_user(BotStoppedSpeakingFrame())
        assert await waiter is True

    @pytest.mark.asyncio
    async def test_stale_stop_from_earlier_speech_does_not_complete_wait(self):
        """A message queued behind in-flight speech must wait for its own turn."""
        engine = make_engine()
        # Speech from a previous utterance is already playing.
        await engine.should_mute_user(BotStartedSpeakingFrame())

        engine.arm_speech_playback()
        waiter = asyncio.create_task(engine.wait_for_speech_playback())

        # The previous utterance finishes - not the one we armed for.
        await engine.should_mute_user(BotStoppedSpeakingFrame())
        await asyncio.sleep(0)
        assert not waiter.done()

        await engine.should_mute_user(BotStartedSpeakingFrame())
        await engine.should_mute_user(BotStoppedSpeakingFrame())
        assert await waiter is True

    @pytest.mark.asyncio
    async def test_wait_gives_up_when_speech_never_starts(self):
        """A broken TTS must not block the transfer indefinitely."""
        engine = make_engine()
        engine.arm_speech_playback()

        assert await engine.wait_for_speech_playback(start_timeout=0.01) is False

    @pytest.mark.asyncio
    async def test_wait_gives_up_when_speech_never_finishes(self):
        engine = make_engine()
        engine.arm_speech_playback()
        await engine.should_mute_user(BotStartedSpeakingFrame())

        assert (
            await engine.wait_for_speech_playback(
                start_timeout=1.0, playback_timeout=0.01
            )
            is False
        )


class RecordingEngine:
    """Engine stub that records the order of playback and transfer steps."""

    def __init__(self):
        self.events: List[Any] = []
        self._workflow_run_id = 1
        self._gathered_context: Dict[str, Any] = {}
        self._call_context_vars: Dict[str, Any] = {}
        self._audio_config = None
        self._fetch_recording_audio = None
        self.task = SimpleNamespace(queue_frame=self._queue_frame)

    async def _queue_frame(self, frame):
        if isinstance(frame, TTSSpeakFrame):
            self.events.append(("speak", frame.text))
        else:
            self.events.append(("frame", type(frame).__name__))

    def arm_speech_playback(self):
        self.events.append("arm")

    async def wait_for_speech_playback(self, **_kwargs):
        self.events.append("wait_for_playback")
        return True

    async def _get_organization_id(self):
        return 1

    async def flush_variable_extraction(self):
        return None

    def record_call_disposition(self, disposition):
        self.events.append(("disposition", disposition))

    async def end_call_with_reason(self, reason, abort_immediately=False):
        self.events.append(("end_call", reason))

    def set_mute_pipeline(self, value):
        return None


class TransferToolModel:
    def __init__(self, custom_message: str = "Transferring you now."):
        self.tool_uuid = "transfer-uuid"
        self.name = "Transfer Call"
        self.description = "Transfer the call"
        self.category = ToolCategory.TRANSFER_CALL.value
        self.definition = {
            "schema_version": 1,
            "type": "transfer_call",
            "config": {
                "destination_source": "dynamic",
                "resolver": {"type": "context_mapping"},
                "messageType": "custom",
                "customMessage": custom_message,
            },
        }


@pytest.mark.asyncio
async def test_external_pbx_transfer_waits_for_message_playback():
    """The PBX transfer API must not fire until the message has been heard."""
    engine = RecordingEngine()
    manager = CustomToolManager(engine)
    tool = TransferToolModel()
    handler = manager._create_transfer_call_handler(tool, "transfer_call")

    workflow_run = SimpleNamespace(
        mode=WorkflowRunMode.ARI.value,
        initial_context={"external_pbx_call": {"type": "vicidial", "lead_id": "42"}},
        gathered_context={"call_id": "1786379595.10"},
    )

    async def fake_transfer_external_pbx_call(**_kwargs):
        engine.events.append("pbx_transfer")
        return {"status": "success", "action": "external_pbx_transfer"}

    provider = SimpleNamespace(
        transfer_external_pbx_call=fake_transfer_external_pbx_call,
        supports_transfers=lambda: True,
        validate_config=lambda: True,
    )

    result_callback = AsyncMock()
    params = SimpleNamespace(arguments={}, result_callback=result_callback)

    with (
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.db_client.get_workflow_run_by_id",
            AsyncMock(return_value=workflow_run),
        ),
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.db_client.get_workflow_run_configurations",
            AsyncMock(return_value={"external_pbx_field_mappings": []}),
        ),
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.db_client.update_workflow_run",
            AsyncMock(),
        ),
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_telephony_provider_for_run",
            AsyncMock(return_value=provider),
        ),
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.resolve_transfer_config",
            AsyncMock(
                return_value=ResolvedTransferConfig(
                    destination="Florida",
                    timeout_seconds=30,
                    source="context_mapping",
                )
            ),
        ),
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.asyncio.sleep",
            AsyncMock(),
        ),
    ):
        await handler(params)

    assert engine.events[:4] == [
        "arm",
        ("speak", "Transferring you now."),
        "wait_for_playback",
        "pbx_transfer",
    ], f"unexpected ordering: {engine.events}"


@pytest.mark.asyncio
async def test_external_pbx_transfer_without_message_does_not_wait():
    """With no message configured the transfer should not stall on playback."""
    engine = RecordingEngine()
    manager = CustomToolManager(engine)
    tool = TransferToolModel()
    tool.definition["config"]["messageType"] = "none"
    handler = manager._create_transfer_call_handler(tool, "transfer_call")

    workflow_run = SimpleNamespace(
        mode=WorkflowRunMode.ARI.value,
        initial_context={"external_pbx_call": {"type": "vicidial", "lead_id": "42"}},
        gathered_context={"call_id": "1786379595.10"},
    )

    async def fake_transfer_external_pbx_call(**_kwargs):
        engine.events.append("pbx_transfer")
        return {"status": "success", "action": "external_pbx_transfer"}

    provider = SimpleNamespace(
        transfer_external_pbx_call=fake_transfer_external_pbx_call,
        supports_transfers=lambda: True,
        validate_config=lambda: True,
    )

    params = SimpleNamespace(arguments={}, result_callback=AsyncMock())

    with (
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.db_client.get_workflow_run_by_id",
            AsyncMock(return_value=workflow_run),
        ),
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.db_client.get_workflow_run_configurations",
            AsyncMock(return_value={"external_pbx_field_mappings": []}),
        ),
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.db_client.update_workflow_run",
            AsyncMock(),
        ),
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_telephony_provider_for_run",
            AsyncMock(return_value=provider),
        ),
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.resolve_transfer_config",
            AsyncMock(
                return_value=ResolvedTransferConfig(
                    destination="Florida",
                    timeout_seconds=30,
                    source="context_mapping",
                )
            ),
        ),
        patch(
            "api.services.workflow.pipecat_engine_custom_tools.asyncio.sleep",
            AsyncMock(),
        ),
    ):
        await handler(params)

    assert "wait_for_playback" not in engine.events
    assert "pbx_transfer" in engine.events


class TestTransferDispositionRace:
    """The PBX drops our media leg ~100ms after its transfer API returns.

    ``on_client_disconnected`` therefore fires while the transfer handler is
    still waiting out ``_TRANSFER_POST_HANDOFF_DELAY_SECS``, and whichever path
    reaches ``end_call_with_reason`` first wins the disposition -- the other is
    a no-op. Every transferred call was being recorded as ``user_hangup``
    because of it.
    """

    @pytest.mark.asyncio
    async def test_recorded_disposition_survives_a_later_hangup(self):
        """A disconnect after the stamp must not relabel the call."""
        engine = make_engine()
        engine.task = SimpleNamespace(queue_frame=AsyncMock())

        engine.record_call_disposition(EndTaskReason.CALL_TRANSFERRED.value)

        with (
            patch.object(
                PipecatEngine, "perform_final_variable_extraction", AsyncMock()
            ),
            patch(
                "api.services.workflow.pipecat_engine.db_client.update_workflow_run",
                AsyncMock(),
            ),
        ):
            await engine.end_call_with_reason(
                EndTaskReason.USER_HANGUP.value, abort_immediately=True
            )

        context = await engine.get_gathered_context()
        assert context["call_disposition"] == EndTaskReason.CALL_TRANSFERRED.value
        assert context["mapped_call_disposition"] == (
            EndTaskReason.CALL_TRANSFERRED.value
        )
        assert EndTaskReason.USER_HANGUP.value not in context["call_tags"]

    @pytest.mark.asyncio
    async def test_an_untransferred_disconnect_is_still_a_user_hangup(self):
        """The fallback must stay intact for calls the caller really drops."""
        engine = make_engine()
        engine.task = SimpleNamespace(queue_frame=AsyncMock())

        with (
            patch.object(
                PipecatEngine, "perform_final_variable_extraction", AsyncMock()
            ),
            patch(
                "api.services.workflow.pipecat_engine.db_client.update_workflow_run",
                AsyncMock(),
            ),
        ):
            await engine.end_call_with_reason(
                EndTaskReason.USER_HANGUP.value, abort_immediately=True
            )

        context = await engine.get_gathered_context()
        assert context["call_disposition"] == EndTaskReason.USER_HANGUP.value

    @pytest.mark.asyncio
    async def test_transfer_stamps_disposition_before_the_settle_delay(self):
        """Stamping after the delay loses the race with the disconnect."""
        engine = RecordingEngine()
        manager = CustomToolManager(engine)
        tool = TransferToolModel()
        handler = manager._create_transfer_call_handler(tool, "transfer_call")

        workflow_run = SimpleNamespace(
            mode=WorkflowRunMode.ARI.value,
            initial_context={
                "external_pbx_call": {"type": "vicidial", "lead_id": "42"}
            },
            gathered_context={"call_id": "1786379595.10"},
        )

        async def fake_transfer_external_pbx_call(**_kwargs):
            engine.events.append("pbx_transfer")
            return {"status": "success", "action": "external_pbx_transfer"}

        provider = SimpleNamespace(
            transfer_external_pbx_call=fake_transfer_external_pbx_call,
            supports_transfers=lambda: True,
            validate_config=lambda: True,
        )
        params = SimpleNamespace(arguments={}, result_callback=AsyncMock())

        async def recording_sleep(_seconds):
            engine.events.append("settle_delay")

        with (
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client.get_workflow_run_by_id",
                AsyncMock(return_value=workflow_run),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client.get_workflow_run_configurations",
                AsyncMock(return_value={"external_pbx_field_mappings": []}),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client.update_workflow_run",
                AsyncMock(),
            ) as update_run,
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.get_telephony_provider_for_run",
                AsyncMock(return_value=provider),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.resolve_transfer_config",
                AsyncMock(
                    return_value=ResolvedTransferConfig(
                        destination="Florida",
                        timeout_seconds=30,
                        source="context_mapping",
                    )
                ),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.asyncio.sleep",
                recording_sleep,
            ),
        ):
            await handler(params)

        assert engine.events.index(("disposition", "call_transferred")) < (
            engine.events.index("settle_delay")
        ), f"disposition must be stamped before the delay: {engine.events}"
        # It also has to reach the DB, since integrations read the run row.
        persisted = update_run.await_args.kwargs["gathered_context"]
        assert persisted["call_disposition"] == "call_transferred"
        assert persisted["mapped_call_disposition"] == "call_transferred"
