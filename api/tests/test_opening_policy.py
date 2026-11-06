"""Voicemail detection changes classification, not the human opening lifecycle."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import CancelFrame

from api.services.pipecat.answer_classification import MachineSubtype
from api.tests.test_answer_supervisor_late_speech import late_call, until


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
@pytest.mark.parametrize("utterance", ["Hello.", "I need help with my appointment."])
async def test_early_caller_turn_is_retained_and_followed_by_one_greeting(
    simple_workflow, enabled, greeting_type, utterance
):
    async with late_call(
        simple_workflow,
        greeting_type,
        enabled=enabled,
        turn_start_strategy="min_words",
        min_words=1,
        listening_window_ms=5000,
        wait_for_output=False,
    ) as c:
        await c.start()
        await c.partial(utterance.rstrip("."))
        await c.stop(utterance)
        # A completed turn ends the silence window early in both modes.
        await asyncio.wait_for(c.output.writing.wait(), 1)
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.llm.get_current_step() == int(greeting_type == "llm")
        assert c.context.messages == [
            {"role": "user", "content": utterance},
            {"role": "assistant", "content": "Welcome."},
        ]
        assert [
            event["payload"]["text"]
            for event in await c.persisted()
            if event["type"] in {"rtf-user-transcription", "rtf-bot-text"}
        ] == [utterance, "Welcome."]
        assert not c.supervisor.blocks_workflow
        assert c.output.interruptions == 0

        await c.say("Can you check my appointment?")
        await until(lambda: c.llm.get_current_step() == 1 + int(greeting_type == "llm"))
        assert c.generation_messages[-1] == [
            {"role": "user", "content": utterance},
            {"role": "assistant", "content": "Welcome."},
            {"role": "user", "content": "Can you check my appointment?"},
        ]
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_speech_spanning_the_deadline_waits_for_its_completed_turn(
    simple_workflow, enabled
):
    classifier = AsyncMock(return_value=MachineSubtype.CONVERSATION)
    async with late_call(
        simple_workflow,
        enabled=enabled,
        listening_window_ms=50,
        classify=classifier,
        wait_for_output=False,
    ) as c:
        await c.start()
        await asyncio.sleep(0.15)
        assert not c.output.writing.is_set()
        assert not c.action.done()
        assert not c.generation_messages
        await c.stop("I need help with my appointment.")
        await asyncio.wait_for(c.output.writing.wait(), 1)
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.context.messages == [
            {"role": "user", "content": "I need help with my appointment."},
            {"role": "assistant", "content": "Welcome."},
        ]
        assert not c.generation_messages
        assert classifier.await_count == int(enabled)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_machine_policy_only_intercepts_when_detection_is_enabled(
    simple_workflow, enabled
):
    async with late_call(
        simple_workflow,
        enabled=enabled,
        listening_window_ms=5000,
        wait_for_output=False,
    ) as c:
        text = "Please leave a message after the tone."
        await c.say(text)
        if not enabled:
            await asyncio.wait_for(c.output.writing.wait(), 1)
            c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        if enabled:
            c.engine.end_call_with_reason.assert_awaited_once_with(
                "voicemail_detected", abort_immediately=True
            )
            assert not c.output.writing.is_set()
        else:
            c.engine.end_call_with_reason.assert_not_awaited()
            assert c.context.messages == [
                {"role": "user", "content": text},
                {"role": "assistant", "content": "Welcome."},
            ]
        assert not c.generation_messages


@pytest.mark.asyncio
async def test_missing_transcript_without_detection_releases_opening_without_hangup(
    simple_workflow,
):
    async with late_call(
        simple_workflow,
        enabled=False,
        listening_window_ms=50,
        machine_utterance_cap_ms=100,
        wait_for_output=False,
    ) as c:
        await c.start()
        await asyncio.wait_for(c.output.writing.wait(), 1)
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert not c.supervisor.blocks_workflow
        assert not c.generation_messages
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_empty_transcript_cannot_cancel_the_opening_fallback(
    simple_workflow, enabled
):
    async with late_call(
        simple_workflow,
        enabled=enabled,
        turn_start_strategy="min_words",
        min_words=1,
        listening_window_ms=50,
        machine_utterance_cap_ms=100,
        wait_for_output=False,
    ) as c:
        await c.start()
        await c.stop("")
        if not enabled:
            await asyncio.wait_for(c.output.writing.wait(), 1)
            c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert not c.generation_messages
        if enabled:
            c.engine.end_call_with_reason.assert_awaited_once_with(
                "machine_timeout", abort_immediately=True
            )
        else:
            assert not c.supervisor.blocks_workflow
            c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_disconnect_during_initial_listening_does_not_play_a_greeting(
    simple_workflow, enabled
):
    async with late_call(
        simple_workflow,
        enabled=enabled,
        listening_window_ms=5000,
        wait_for_output=False,
    ) as c:
        await c.worker.queue_frame(CancelFrame())
        await asyncio.wait_for(c.action, 1)
        assert not c.output.writing.is_set()
        assert not c.generation_messages
