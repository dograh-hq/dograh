"""Recording instructions and routing follow the same formatted node prompt."""

from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.recording_audio_cache import RecordingAudio
from api.services.pipecat.recording_router_processor import RecordingRouterProcessor
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_context_composer import (
    RECORDING_RESPONSE_MODE_INSTRUCTIONS,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", [False, True])
@pytest.mark.parametrize(
    ("has_recordings", "node_prompt", "context", "uses_recordings"),
    [
        (True, "Speak normally.", {}, False),
        (True, "RECORDING_ID: rec123", {}, True),
        (True, "{{ clip }}", {"clip": "RECORDING_ID: rec123"}, True),
        (
            True,
            "{{ clip | fallback:RECORDING_ID: rec123 }}",
            {"clip": "Speak normally."},
            False,
        ),
        (False, "RECORDING_ID: rec123", {}, False),
    ],
)
async def test_routing_follows_node_prompt_across_transitions(
    three_node_workflow,
    destination,
    has_recordings,
    node_prompt,
    context,
    uses_recordings,
):
    llm = Mock()
    llm._update_settings = AsyncMock()
    engine = PipecatEngine(
        workflow=three_node_workflow,
        llm=llm,
        call_context_vars=context,
        has_recordings=has_recordings,
    )
    agent = engine.active_agent
    if destination:
        # Destination preparation runs before this visit becomes active.
        agent = replace(agent, visit_id="destination")
    fetch = AsyncMock(return_value=RecordingAudio(audio=b"\x00\x01" * 80))
    router = RecordingRouterProcessor(
        audio_sample_rate=16_000, fetch_recording_audio=fetch
    )
    router.push_frame = AsyncMock()
    agent.recording_router = router
    three_node_workflow.nodes["agent"].prompt = node_prompt

    # Ordinary node -> potentially recorded node -> ordinary node again.
    for node_id, recording_enabled in (
        ("start", False),
        ("agent", uses_recordings),
        ("start", False),
    ):
        await engine._prepare_node(agent, three_node_workflow.nodes[node_id])
        assert (
            RECORDING_RESPONSE_MODE_INSTRUCTIONS in agent.system_prompt
        ) == recording_enabled
        assert (
            llm._update_settings.call_args.args[0].system_instruction
            == agent.system_prompt
        )

        direction = FrameDirection.DOWNSTREAM
        await router.process_frame(LLMFullResponseStartFrame(), direction)
        router.push_frame.reset_mock()
        fetch.reset_mock()
        if recording_enabled:
            await router.process_frame(LLMTextFrame("●"), direction)
            await router.process_frame(LLMTextFrame(" rec123 transcript"), direction)
            fetch.assert_awaited_once_with(recording_id="rec123")
        else:
            frame = LLMTextFrame("Speech without a response marker.")
            await router.process_frame(frame, direction)
            # Assert delivery before sending the end frame: no token buffering.
            router.push_frame.assert_awaited_once_with(frame, direction)
            fetch.assert_not_awaited()
        await router.process_frame(LLMFullResponseEndFrame(), direction)
