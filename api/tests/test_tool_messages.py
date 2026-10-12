"""Announcement selection and deadlines without network or provider services."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter, ValidationError

from api.schemas.tool import CreateToolRequest, ToolMessages
from api.services.pipecat.speech_playback import PlaybackOutcome, SpeechPlaybackTracker
from api.services.tool_management import ToolManagementError, validate_tool_references
from api.services.workflow.tool_execution import ToolExecutionOwner
from api.services.workflow.tool_messages import tool_messages


def plan(text="Checking.", delays=()):
    messages = [{"event": "start", "type": "text", "text": text}] if text else []
    messages += [
        {
            "event": "delayed",
            "after_ms": delay,
            "type": "text",
            "text": f"Waiting {delay}.",
        }
        for delay in delays
    ]
    return TypeAdapter(ToolMessages).validate_python(messages)


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
    messages = TypeAdapter(ToolMessages).validate_python(
        [
            {"event": "start", "type": "text", "text": "First."},
            {"event": "start", "type": "audio", "recording_id": 42},
        ]
    )
    execution, release = harness.start("recording", messages)
    assert await harness.next_message() == 42
    release.set()
    await execution.task
    assert harness.spoken.empty()


def test_runtime_reads_only_canonical_messages():
    assert tool_messages({"type": "http_api", "messages": []}) == []
    assert (
        tool_messages({"type": "http_api", "config": {"customMessage": "Legacy"}}) == []
    )
    assert (
        tool_messages({"messages": [{"event": "start", "type": "text", "text": "Hi"}]})[
            0
        ].text
        == "Hi"
    )


@pytest.mark.parametrize(
    "messages",
    [
        {"start": {"variants": []}},
        [{"event": "start", "type": "text", "text": "  "}],
        [{"event": "start", "type": "audio", "recording_id": 0}],
        [{"event": "start", "type": "text", "text": "Hi", "wait_for_playback": True}],
        [{"event": "start", "type": "text", "text": "Hi", "after_ms": 10}],
        [{"event": "delayed", "type": "text", "text": "Hi"}],
        [{"event": "delayed", "type": "text", "text": "Hi", "after_ms": 0}],
    ],
)
def test_invalid_plans_are_rejected(messages):
    with pytest.raises(ValidationError):
        TypeAdapter(ToolMessages).validate_python(messages)


@pytest.mark.asyncio
async def test_same_delay_entries_are_variants_not_repeated_updates(
    harness, monkeypatch
):
    monkeypatch.setattr(
        "api.services.workflow.tool_messages.random.choice",
        lambda variants: variants[-1],
    )
    messages = plan(None, [20, 10])
    messages += TypeAdapter(ToolMessages).validate_python(
        [
            {
                "event": "delayed",
                "after_ms": 10,
                "type": "text",
                "text": "Alternative.",
            },
        ]
    )
    execution, release = harness.start("variants", messages)
    assert await harness.next_message() == "Alternative."
    assert await harness.next_message() == "Waiting 20."
    release.set()
    await execution.task
    assert harness.spoken.empty()


def definition(category, messages):
    config = {
        "http_api": {"method": "POST", "url": "https://example.com"},
        "mcp": {"url": "https://example.com/mcp"},
        "transfer_agent": {"workflow_id": 1},
    }.get(category, {})
    return CreateToolRequest.model_validate(
        {
            "name": "Tool",
            "definition": {
                "type": category,
                "messages": messages,
                **({"config": config} if category != "calculator" else {}),
            },
        }
    )


@pytest.mark.parametrize(
    "category",
    ["http_api", "mcp", "end_call", "transfer_call", "transfer_agent", "calculator"],
)
def test_all_tool_definitions_have_a_shared_empty_list(category):
    assert definition(category, []).definition.messages == []


@pytest.mark.parametrize("category", ["end_call", "transfer_call", "transfer_agent"])
def test_control_tools_accept_one_start_and_reject_variants_or_delays(category):
    start = {"event": "start", "type": "text", "text": "Goodbye."}
    assert definition(category, [start]).definition.messages[0].text == "Goodbye."
    for messages in ([start, start], [{**start, "event": "delayed", "after_ms": 1000}]):
        with pytest.raises(ValidationError, match="at most one start"):
            definition(category, messages)


def test_capabilities_and_legacy_api_fields_are_validated():
    audio = {"event": "start", "type": "audio", "recording_id": 42}
    for category in ("end_call", "transfer_call"):
        assert definition(category, [audio]).definition.messages[0].recording_id == 42
    with pytest.raises(ValidationError, match="text messages only"):
        definition("transfer_agent", [audio])
    with pytest.raises(ValidationError, match="500 characters"):
        definition(
            "transfer_agent", [{"event": "start", "type": "text", "text": "x" * 501}]
        )
    with pytest.raises(ValidationError, match="do not support"):
        definition("calculator", [audio])
    with pytest.raises(ValidationError, match="Extra inputs"):
        CreateToolRequest.model_validate(
            {
                "name": "Old",
                "definition": {
                    "type": "end_call",
                    "config": {"customMessage": "Goodbye", "messageType": "custom"},
                },
            }
        )


def test_agent_transfer_preserves_default_handover_unless_explicitly_disabled():
    request = CreateToolRequest.model_validate(
        {
            "name": "Transfer",
            "definition": {"type": "transfer_agent", "config": {"workflow_id": 1}},
        }
    )
    assert request.definition.messages[0].text == (
        "Let me connect you with the right person. One moment please."
    )
    assert definition("transfer_agent", []).definition.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize("accessible_ids", [set(), {42}, {42, 43}])
async def test_recording_references_are_organization_scoped(
    monkeypatch, accessible_ids
):
    lookup = AsyncMock(return_value=accessible_ids)
    monkeypatch.setattr(
        "api.services.tool_management.db_client.get_active_recording_ids", lookup
    )
    definition = {
        "type": "http_api",
        "config": {},
        "messages": [
            {"event": "start", "type": "audio", "recording_id": recording_id}
            for recording_id in (42, 43, 42)
        ],
    }
    if accessible_ids == {42, 43}:
        await validate_tool_references(definition, organization_id=7)
    else:
        with pytest.raises(ToolManagementError, match="organization"):
            await validate_tool_references(definition, organization_id=7)
    lookup.assert_awaited_once_with({42, 43}, 7)


@pytest.mark.asyncio
async def test_bulk_recording_lookup_excludes_other_orgs_and_deleted_audio(db_session):
    user, _ = await db_session.get_or_create_user_by_provider_id(
        "tool_message_recordings"
    )
    organization, _ = await db_session.get_or_create_organization_by_provider_id(
        "tool_message_recordings", user.id
    )
    other_org, _ = await db_session.get_or_create_organization_by_provider_id(
        "other_tool_message_recordings", user.id
    )
    recordings = []
    for index, org_id in enumerate((organization.id, organization.id, other_org.id)):
        recordings.append(
            await db_session.create_recording(
                recording_id=f"tool-message-{index}",
                organization_id=org_id,
                transcript="Checking.",
                storage_key=f"tool-message-{index}.wav",
                storage_backend="s3",
                created_by=user.id,
            )
        )
    await db_session.delete_recording(recordings[1].recording_id, organization.id)
    assert await db_session.get_active_recording_ids(
        {recording.id for recording in recordings}, organization.id
    ) == {recordings[0].id}
    assert await db_session.get_active_recording_ids(set(), organization.id) == set()
