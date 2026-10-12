"""Announcement selection and deadlines without network or provider services."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.schemas.tool import ToolMessages
from api.services.pipecat.speech_playback import PlaybackOutcome, SpeechPlaybackTracker
from api.services.tool_management import ToolManagementError, validate_tool_references
from api.services.workflow.tool_execution import ToolExecutionOwner
from api.services.workflow.tool_messages import tool_messages


def plan(text="Checking.", delays=()):
    return ToolMessages.model_validate(
        {
            "start": {"variants": [{"type": "text", "text": text}]} if text else None,
            "delayed": [
                {
                    "after_ms": delay,
                    "variants": [{"type": "text", "text": f"Waiting {delay}."}],
                }
                for delay in delays
            ],
        }
    )


class Harness:
    def __init__(self):
        self.agent = SimpleNamespace(visit_id="visit-1")
        self.active = True
        self.speech_playback = SpeechPlaybackTracker()
        self._bot_is_speaking = False
        self._workflow_run_id = 1
        self.spoken = asyncio.Queue()
        self.records = []
        self.owner = ToolExecutionOwner(self, self.agent)
        self.entered = asyncio.Queue()

    def agent_can_act(self, agent):
        return self.active and agent is self.agent

    def record_tool_result(self, record):
        self.records.append(record)

    async def queue_speech(self, **kwargs):
        assert kwargs["origin_agent"] is self.agent
        if kwargs["can_queue"]():
            self.spoken.put_nowait(kwargs.get("text", kwargs.get("recording_pk")))
        speech = self.speech_playback.create()
        speech.finish(PlaybackOutcome.PLAYED)
        return speech

    def start(self, name, messages=None, *, timeout=1):
        release = asyncio.Event()

        async def handler(params):
            self.entered.put_nowait(name)
            await release.wait()
            await params.result_callback({"name": name})

        params = SimpleNamespace(
            tool_call_id=name, function_name=name, arguments={}, result_callback=None
        )
        execution = self.owner.start(handler, params, timeout, messages)
        return execution, release

    async def next_message(self):
        return await asyncio.wait_for(self.spoken.get(), 1)


@pytest.fixture
async def harness():
    h = Harness()
    yield h
    h.owner.stop_messages()
    for execution in h.owner._executions.values():
        if execution.task and not execution.task.done():
            execution.task.cancel()
    await h.owner.finish()


@pytest.mark.asyncio
async def test_first_configured_invocation_owns_messages_until_group_drains(harness):
    h = harness
    _silent, silent_release = h.start("silent")
    selected, selected_release = h.start("selected", plan("Selected.", [50]))
    _other, other_release = h.start("other", plan("Other.", [10]))
    assert await h.next_message() == "Selected."
    assert {await h.entered.get() for _ in range(3)} == {"silent", "selected", "other"}
    selected_release.set()
    await selected.task
    # A later streamed call cannot take ownership while its siblings still run.
    _late, late_release = h.start("late", plan("Late."))
    await asyncio.sleep(0.07)
    assert h.spoken.empty()
    for release in (silent_release, other_release, late_release):
        release.set()
    await h.owner.finish()
    new, new_release = h.start("next-group", plan("Next."))
    assert await h.next_message() == "Next."
    new_release.set()
    await new.task


@pytest.mark.asyncio
async def test_delay_only_invocation_owns_messages_over_later_start_message(harness):
    selected, selected_release = harness.start("selected", plan(None, [10]))
    other, other_release = harness.start("other", plan("Other."))
    assert await harness.next_message() == "Waiting 10."
    assert harness.spoken.empty()
    selected_release.set()
    other_release.set()
    await asyncio.gather(selected.task, other.task)


@pytest.mark.asyncio
async def test_delays_fire_once_and_stop_on_result(harness):
    execution, release = harness.start("slow", plan(delays=[10, 30, 200]))
    assert await harness.next_message() == "Checking."
    assert await harness.next_message() == "Waiting 10."
    assert await harness.next_message() == "Waiting 30."
    release.set()
    await execution.task
    await asyncio.sleep(0.22)
    assert harness.spoken.empty()
    assert execution.result == {"name": "slow"}


@pytest.mark.asyncio
async def test_busy_speech_drops_obsolete_delays_and_does_not_block_request(harness):
    harness._bot_is_speaking = True
    execution, release = harness.start("slow", plan(delays=[10, 20]))
    assert await harness.next_message() == "Checking."
    assert await harness.entered.get() == "slow"
    await asyncio.sleep(0.04)
    assert harness.spoken.empty()
    harness._bot_is_speaking = False
    assert await harness.next_message() == "Waiting 20."
    release.set()
    await execution.task


@pytest.mark.asyncio
async def test_completion_while_speech_busy_discards_delayed_update(harness):
    harness._bot_is_speaking = True
    execution, release = harness.start("quick", plan(delays=[10]))
    assert await harness.next_message() == "Checking."
    await asyncio.sleep(0.02)
    release.set()
    await execution.task
    harness._bot_is_speaking = False
    await asyncio.sleep(0.06)
    assert harness.spoken.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("retired", [False, True])
async def test_stop_messages_preserves_accepted_request(harness, retired):
    execution, release = harness.start("save", plan(delays=[20]))
    assert await harness.next_message() == "Checking."
    if retired:
        harness.active = False
    harness.owner.stop_messages()
    await asyncio.sleep(0.04)
    assert not execution.task.done()
    assert harness.spoken.empty()
    release.set()
    await execution.task
    assert harness.records[0]["result"] == {"name": "save"}


@pytest.mark.asyncio
async def test_timeout_cancels_remaining_delays(harness):
    execution, _ = harness.start("timeout", plan(delays=[70]), timeout=0.02)
    assert await harness.next_message() == "Checking."
    await execution.task
    await asyncio.sleep(0.08)
    assert harness.spoken.empty()
    assert harness.records[0]["status"] == "timeout"


@pytest.mark.asyncio
async def test_duplicate_call_neither_executes_nor_announces_again(harness):
    first, release = harness.start("same", plan())
    again, _ = harness.start("same", plan("Duplicate."))
    assert first is again
    assert await harness.next_message() == "Checking."
    release.set()
    await first.task
    assert harness.spoken.empty()
    assert len(harness.records) == 1


@pytest.mark.asyncio
async def test_selects_one_variant_and_audio_uses_recording_primary_key(
    harness, monkeypatch
):
    monkeypatch.setattr(
        "api.services.workflow.tool_messages.random.choice",
        lambda variants: variants[-1],
    )
    messages = ToolMessages.model_validate(
        {
            "start": {
                "variants": [
                    {"type": "text", "text": "First."},
                    {"type": "audio", "recording_id": 42},
                ]
            }
        }
    )
    execution, release = harness.start("recording", messages)
    assert await harness.next_message() == 42
    release.set()
    await execution.task
    assert harness.spoken.empty()


def test_legacy_messages_and_explicit_disable():
    definition = {"type": "http_api", "config": {"customMessage": "Wait."}}
    assert tool_messages(definition).start.variants[0].text == "Wait."
    assert tool_messages({**definition, "messages": {}}) is None
    assert (
        tool_messages({**definition, "messages": {"start": None, "delayed": []}})
        is None
    )
    assert (
        tool_messages(
            {
                "type": "http_api",
                "config": {
                    "customMessageType": "audio",
                    "customMessageRecordingId": "12",
                },
            }
        )
        .start.variants[0]
        .recording_id
        == 12
    )
    assert (
        tool_messages({"type": "http_api", "config": {"customMessage": "   "}}) is None
    )


@pytest.mark.parametrize(
    "messages",
    [
        {"start": {"variants": []}},
        {"start": {"variants": [{"type": "text", "text": "  "}]}},
        {"start": {"variants": [{"type": "audio", "recording_id": 0}]}},
        {
            "start": {
                "variants": [{"type": "text", "text": "Hi"}],
                "wait_for_playback": True,
            }
        },
        {"delayed": [{"after_ms": 0, "variants": [{"type": "text", "text": "Hi"}]}]},
    ],
)
def test_invalid_plans_are_rejected(messages):
    with pytest.raises(ValidationError):
        ToolMessages.model_validate(messages)


def test_timings_are_unique_and_sorted():
    assert [m.after_ms for m in plan(delays=[50, 10]).delayed] == [10, 50]
    with pytest.raises(ValidationError, match="unique"):
        plan(delays=[10, 10])


@pytest.mark.asyncio
async def test_recording_references_are_organization_scoped(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "api.services.tool_management.db_client.get_recording_by_id", lookup
    )
    definition = {
        "type": "http_api",
        "config": {},
        "messages": {"start": {"variants": [{"type": "audio", "recording_id": 42}]}},
    }
    with pytest.raises(ToolManagementError, match="organization"):
        await validate_tool_references(definition, organization_id=7)
    lookup.assert_awaited_once_with(42, 7)
