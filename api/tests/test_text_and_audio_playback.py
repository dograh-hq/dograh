"""Tests for text and audio playback in greetings, transitions, and tool messages.

Verifies that:
- Text mode produces TTSSpeakFrame
- Audio mode produces TTSStartedFrame -> TTSAudioRawFrame -> TTSStoppedFrame
- Covers: start node greetings, edge transition speech, tool config messages
"""

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
)
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams

from api.services.pipecat.recording_audio_cache import RecordingAudio
from api.services.pipecat.worker_runner import run_pipeline_worker
from api.services.workflow.dto import (
    EdgeDataDTO,
    EndCallNodeData,
    Position,
    ReactFlowDTO,
    RFEdgeDTO,
    RFNodeDTO,
    StartCallNodeData,
)
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
from api.services.workflow.workflow_graph import WorkflowGraph
from pipecat.tests import MockLLMService, MockTTSService

# ─── Constants ──────────────────────────────────────────────────

START_PROMPT = "Start Call System Prompt"
END_PROMPT = "End Call System Prompt"
TEXT_GREETING = "Hello, welcome to our service!"
TEXT_TRANSITION = "Thank you for calling, goodbye!"
AUDIO_GREETING_ID = "rec-greeting-001"
AUDIO_TRANSITION_ID = "101"
FAKE_PCM_AUDIO = b"\x00\x01" * 1000  # Fake 16-bit mono PCM data


# ─── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def text_workflow() -> WorkflowGraph:
    """Start->End workflow with text greeting and text transition speech."""
    dto = ReactFlowDTO(
        nodes=[
            RFNodeDTO(
                id="start",
                type="startCall",
                position=Position(x=0, y=0),
                data=StartCallNodeData(
                    name="Start Call",
                    prompt=START_PROMPT,
                    is_start=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    greeting=TEXT_GREETING,
                    greeting_type="text",
                    extraction_enabled=False,
                ),
            ),
            RFNodeDTO(
                id="end",
                type="endCall",
                position=Position(x=0, y=200),
                data=EndCallNodeData(
                    name="End Call",
                    prompt=END_PROMPT,
                    is_end=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=False,
                ),
            ),
        ],
        edges=[
            RFEdgeDTO(
                id="start-end",
                source="start",
                target="end",
                data=EdgeDataDTO(
                    label="End Call",
                    condition="When the user says end the call",
                    transition_speech=TEXT_TRANSITION,
                    transition_speech_type="text",
                ),
            ),
        ],
    )
    return WorkflowGraph(dto)


@pytest.fixture
def audio_workflow() -> WorkflowGraph:
    """Start->End workflow with audio greeting and audio transition speech."""
    dto = ReactFlowDTO(
        nodes=[
            RFNodeDTO(
                id="start",
                type="startCall",
                position=Position(x=0, y=0),
                data=StartCallNodeData(
                    name="Start Call",
                    prompt=START_PROMPT,
                    is_start=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    greeting_type="audio",
                    greeting_recording_id=AUDIO_GREETING_ID,
                    extraction_enabled=False,
                ),
            ),
            RFNodeDTO(
                id="end",
                type="endCall",
                position=Position(x=0, y=200),
                data=EndCallNodeData(
                    name="End Call",
                    prompt=END_PROMPT,
                    is_end=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=False,
                ),
            ),
        ],
        edges=[
            RFEdgeDTO(
                id="start-end",
                source="start",
                target="end",
                data=EdgeDataDTO(
                    label="End Call",
                    condition="When the user says end the call",
                    transition_speech_type="audio",
                    transition_speech_recording_id=AUDIO_TRANSITION_ID,
                ),
            ),
        ],
    )
    return WorkflowGraph(dto)


# ─── Pipeline Helper ────────────────────────────────────────────


async def run_pipeline_and_capture_frames(
    workflow: WorkflowGraph,
    functions: List[Dict[str, Any]],
    fetch_recording_audio=None,
    num_text_steps: int = 1,
) -> tuple[MockLLMService, LLMContext, list[Frame]]:
    """Run a pipeline with mock tool calls and capture frames queued via task.queue_frame.

    Returns:
        Tuple of (llm, context, list of captured frames).
    """
    first_step_chunks = MockLLMService.create_multiple_function_call_chunks(functions)
    mock_steps = MockLLMService.create_multi_step_responses(
        first_step_chunks, num_text_steps=num_text_steps, step_prefix="Response"
    )

    llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)
    tts = MockTTSService(mock_audio_duration_ms=40, frame_delay=0)
    mock_transport = MockTransport(
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
        ),
    )

    context = LLMContext()
    assistant_params = LLMAssistantAggregatorParams()
    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params
    )

    engine = PipecatEngine(
        llm=llm,
        context=context,
        workflow=workflow,
        call_context_vars={"customer_name": "Test User"},
        workflow_run_id=1,
    )

    transport_output = mock_transport.output()

    if fetch_recording_audio:
        engine.set_fetch_recording_audio(fetch_recording_audio)
        engine.set_transport_output(transport_output)

    pipeline = Pipeline([llm, tts, transport_output, context_aggregator.assistant()])
    task = PipelineWorker(pipeline, params=PipelineParams(), enable_rtvi=False)
    engine.set_task(task)

    # Spy on task.queue_frame and transport_output.queue_frame to capture
    # all frames queued by the engine (audio transitions go via transport output)
    queued_frames: list[Frame] = []
    original_queue_frame = task.queue_frame

    async def capturing_queue_frame(frame):
        queued_frames.append(frame)
        await original_queue_frame(frame)

    task.queue_frame = capturing_queue_frame

    if fetch_recording_audio:
        original_transport_queue = transport_output.queue_frame

        async def _spy_transport_queue(frame, *args, **kwargs):
            queued_frames.append(frame)
            await original_transport_queue(frame, *args, **kwargs)

        transport_output.queue_frame = _spy_transport_queue

    with (
        patch(
            "api.db:db_client.get_organization_id_by_workflow_run_id",
            new_callable=AsyncMock,
            return_value=1,
        ),
    ):

        async def run():
            await run_pipeline_worker(task)

        async def initialize():
            await asyncio.sleep(0.01)
            await engine.initialize()
            await engine.set_node(engine.workflow.start_node_id)
            await engine.llm.queue_frame(LLMContextFrame(engine.context))

        await asyncio.gather(run(), initialize())

    return llm, context, queued_frames


# ─── Tests: Start Greeting ──────────────────────────────────────


class TestStartGreeting:
    """Unit tests for PipecatEngine.get_start_greeting()."""

    def test_text_greeting_returns_text_tuple(self, text_workflow: WorkflowGraph):
        """Text greeting config should return ('text', rendered_text)."""
        engine = PipecatEngine(
            workflow=text_workflow,
            call_context_vars={},
            workflow_run_id=1,
        )
        result = engine.get_start_greeting()
        assert result == ("text", TEXT_GREETING)

    def test_audio_greeting_returns_audio_tuple(self, audio_workflow: WorkflowGraph):
        """Audio greeting config should return ('audio', recording_id)."""
        engine = PipecatEngine(
            workflow=audio_workflow,
            call_context_vars={},
            workflow_run_id=1,
        )
        result = engine.get_start_greeting()
        assert result == ("audio", AUDIO_GREETING_ID)

    def test_no_greeting_returns_none(self):
        """No greeting configured should return None."""
        dto = ReactFlowDTO(
            nodes=[
                RFNodeDTO(
                    id="start",
                    type="startCall",
                    position=Position(x=0, y=0),
                    data=StartCallNodeData(
                        name="Start",
                        prompt="Prompt",
                        is_start=True,
                        add_global_prompt=False,
                        extraction_enabled=False,
                    ),
                ),
                RFNodeDTO(
                    id="end",
                    type="endCall",
                    position=Position(x=0, y=200),
                    data=EndCallNodeData(
                        name="End",
                        prompt="End",
                        is_end=True,
                        add_global_prompt=False,
                        extraction_enabled=False,
                    ),
                ),
            ],
            edges=[
                RFEdgeDTO(
                    id="e",
                    source="start",
                    target="end",
                    data=EdgeDataDTO(label="End", condition="End"),
                ),
            ],
        )
        engine = PipecatEngine(
            workflow=WorkflowGraph(dto),
            call_context_vars={},
            workflow_run_id=1,
        )
        assert engine.get_start_greeting() is None

    def test_text_greeting_renders_template_variables(self):
        """Text greeting with {{variable}} placeholders should be rendered."""
        dto = ReactFlowDTO(
            nodes=[
                RFNodeDTO(
                    id="start",
                    type="startCall",
                    position=Position(x=0, y=0),
                    data=StartCallNodeData(
                        name="Start",
                        prompt="Prompt",
                        is_start=True,
                        add_global_prompt=False,
                        greeting="Hello {{customer_name}}!",
                        greeting_type="text",
                        extraction_enabled=False,
                    ),
                ),
                RFNodeDTO(
                    id="end",
                    type="endCall",
                    position=Position(x=0, y=200),
                    data=EndCallNodeData(
                        name="End",
                        prompt="End",
                        is_end=True,
                        add_global_prompt=False,
                        extraction_enabled=False,
                    ),
                ),
            ],
            edges=[
                RFEdgeDTO(
                    id="e",
                    source="start",
                    target="end",
                    data=EdgeDataDTO(label="End", condition="End"),
                ),
            ],
        )
        engine = PipecatEngine(
            workflow=WorkflowGraph(dto),
            call_context_vars={"customer_name": "Alice"},
            workflow_run_id=1,
        )
        result = engine.get_start_greeting()
        assert result == ("text", "Hello Alice!")

    @pytest.mark.asyncio
    async def test_queue_node_opening_queues_text_greeting(
        self, text_workflow: WorkflowGraph
    ):
        """Fresh node entry with a greeting should queue TTS and skip LLM bootstrap."""
        llm = Mock()
        llm.queue_frame = AsyncMock()
        task = Mock()
        task.queue_frame = AsyncMock()

        engine = PipecatEngine(
            llm=llm,
            context=LLMContext(),
            workflow=text_workflow,
            call_context_vars={},
            workflow_run_id=1,
        )
        engine.set_task(task)

        result = await engine.queue_node_opening(
            node_id=text_workflow.start_node_id,
            previous_node_id=None,
            generate_if_no_greeting=True,
        )

        assert result == "greeting"
        llm.queue_frame.assert_not_awaited()
        queued_frame = task.queue_frame.await_args.args[0]
        assert isinstance(queued_frame, TTSSpeakFrame)
        assert queued_frame.text == TEXT_GREETING
        assert queued_frame.append_to_context is True

    @pytest.mark.asyncio
    async def test_queue_node_opening_falls_back_to_llm_without_greeting(self):
        """When a node has no greeting, the engine should queue initial LLM generation."""
        dto = ReactFlowDTO(
            nodes=[
                RFNodeDTO(
                    id="start",
                    type="startCall",
                    position=Position(x=0, y=0),
                    data=StartCallNodeData(
                        name="Start",
                        prompt="Prompt",
                        is_start=True,
                        add_global_prompt=False,
                        extraction_enabled=False,
                    ),
                ),
                RFNodeDTO(
                    id="end",
                    type="endCall",
                    position=Position(x=0, y=200),
                    data=EndCallNodeData(
                        name="End",
                        prompt="End",
                        is_end=True,
                        add_global_prompt=False,
                        extraction_enabled=False,
                    ),
                ),
            ],
            edges=[
                RFEdgeDTO(
                    id="e",
                    source="start",
                    target="end",
                    data=EdgeDataDTO(label="End", condition="End"),
                ),
            ],
        )
        workflow = WorkflowGraph(dto)
        context = LLMContext()
        llm = Mock()
        llm.queue_frame = AsyncMock()
        task = Mock()
        task.queue_frame = AsyncMock()

        engine = PipecatEngine(
            llm=llm,
            context=context,
            workflow=workflow,
            call_context_vars={},
            workflow_run_id=1,
        )
        engine.set_task(task)

        result = await engine.queue_node_opening(
            node_id=workflow.start_node_id,
            previous_node_id=None,
            generate_if_no_greeting=True,
        )

        assert result == "llm"
        task.queue_frame.assert_not_awaited()
        queued_frame = llm.queue_frame.await_args.args[0]
        assert isinstance(queued_frame, LLMContextFrame)
        assert queued_frame.context is context


# ─── Tests: Transition Speech (Pipeline) ────────────────────────


class TestTransitionSpeech:
    """Pipeline tests for edge transition speech (text and audio)."""

    @pytest.mark.asyncio
    async def test_text_transition_queues_tts_speak_frame(
        self, text_workflow: WorkflowGraph
    ):
        """Text transition speech should queue a TTSSpeakFrame with the message."""
        functions = [
            {
                "name": "end_call",
                "arguments": {},
                "tool_call_id": "call_transition",
            },
        ]

        llm, context, queued_frames = await run_pipeline_and_capture_frames(
            workflow=text_workflow,
            functions=functions,
            num_text_steps=2,
        )

        # Pipeline completes: 1st gen on StartNode, 2nd gen on EndNode
        assert llm.get_current_step() == 2

        # Verify TTSSpeakFrame was queued with the transition speech text
        tts_speak_frames = [f for f in queued_frames if isinstance(f, TTSSpeakFrame)]
        transition_frames = [f for f in tts_speak_frames if f.text == TEXT_TRANSITION]
        assert len(transition_frames) == 1, (
            f"Expected one TTSSpeakFrame with text '{TEXT_TRANSITION}', "
            f"got: {[f.text for f in tts_speak_frames]}"
        )

        # No raw audio frames should be queued for text transition
        audio_raw = [f for f in queued_frames if isinstance(f, TTSAudioRawFrame)]
        assert len(audio_raw) == 0

    @pytest.mark.asyncio
    async def test_audio_transition_queues_audio_frames(
        self, audio_workflow: WorkflowGraph
    ):
        """Audio transition speech should queue TTSStarted + TTSAudioRaw + TTSStopped."""
        functions = [
            {
                "name": "end_call",
                "arguments": {},
                "tool_call_id": "call_transition",
            },
        ]

        mock_fetch = AsyncMock(return_value=RecordingAudio(audio=FAKE_PCM_AUDIO))

        llm, context, queued_frames = await run_pipeline_and_capture_frames(
            workflow=audio_workflow,
            functions=functions,
            fetch_recording_audio=mock_fetch,
            num_text_steps=2,
        )

        # Pipeline completes
        assert llm.get_current_step() == 2

        # Verify fetch was called with the correct recording ID
        mock_fetch.assert_called_once_with(recording_pk=int(AUDIO_TRANSITION_ID))

        # Verify the three-frame audio sequence was queued
        started = [f for f in queued_frames if isinstance(f, TTSStartedFrame)]
        audio = [f for f in queued_frames if isinstance(f, TTSAudioRawFrame)]
        stopped = [f for f in queued_frames if isinstance(f, TTSStoppedFrame)]

        assert len(started) >= 1, (
            f"Expected TTSStartedFrame. "
            f"Frame types: {[type(f).__name__ for f in queued_frames]}"
        )
        assert len(audio) >= 1, "Expected TTSAudioRawFrame"
        assert len(stopped) >= 1, "Expected TTSStoppedFrame"

        # Verify audio content
        assert audio[0].audio == FAKE_PCM_AUDIO
        assert audio[0].sample_rate == 16000
        assert audio[0].num_channels == 1

        # Verify context_id consistency across the three frames
        ctx_id = started[0].context_id
        assert ctx_id is not None
        assert audio[0].context_id == ctx_id
        assert stopped[0].context_id == ctx_id

        # No TTSSpeakFrame should be queued for audio transition
        speak = [f for f in queued_frames if isinstance(f, TTSSpeakFrame)]
        assert len(speak) == 0


# ─── Tests: Tool Config Messages ────────────────────────────────


class TestPlayConfigMessage:
    """Unit tests for CustomToolManager._play_config_message."""

    @pytest.fixture
    def mock_engine(self):
        """Create a mock engine with frame capture on task.queue_frame."""
        engine = Mock()
        engine._workflow_run_id = 1
        engine._call_context_vars = {}
        engine._fetch_recording_audio = None
        engine._audio_config = None
        engine.task = Mock()
        engine.llm = Mock()

        # Capture frames queued via task.queue_frame
        engine._queued_frames = []

        async def mock_queue_frame(frame):
            engine._queued_frames.append(frame)

        engine.task.queue_frame = mock_queue_frame

        # Also capture frames queued via transport_output.queue_frame (audio playback)
        engine._transport_output = Mock()
        engine._transport_output.queue_frame = mock_queue_frame
        return engine

    @pytest.mark.asyncio
    async def test_custom_text_queues_tts_speak_frame(self, mock_engine):
        """messageType='custom' queues TTSSpeakFrame with the message text."""
        manager = CustomToolManager(mock_engine)
        config = {"messageType": "custom", "customMessage": "Ending your call now."}

        result = await manager._play_config_message(config)

        assert result is True
        frames = mock_engine._queued_frames
        assert len(frames) == 1
        assert isinstance(frames[0], TTSSpeakFrame)
        assert frames[0].text == "Ending your call now."

    @pytest.mark.asyncio
    async def test_audio_queues_started_raw_stopped_frames(self, mock_engine):
        """messageType='audio' queues TTSStarted + TTSAudioRaw + TTSStopped."""
        mock_fetch = AsyncMock(return_value=RecordingAudio(audio=FAKE_PCM_AUDIO))
        mock_engine._fetch_recording_audio = mock_fetch

        manager = CustomToolManager(mock_engine)
        config = {"messageType": "audio", "audioRecordingId": "201"}

        result = await manager._play_config_message(config)

        assert result is True
        mock_fetch.assert_called_once_with(recording_pk=201)

        frames = mock_engine._queued_frames
        assert len(frames) == 3
        assert isinstance(frames[0], TTSStartedFrame)
        assert isinstance(frames[1], TTSAudioRawFrame)
        assert isinstance(frames[2], TTSStoppedFrame)

        # Verify audio content
        assert frames[1].audio == FAKE_PCM_AUDIO
        assert frames[1].sample_rate == 16000
        assert frames[1].num_channels == 1

        # Context IDs should match across all three frames
        ctx_id = frames[0].context_id
        assert ctx_id is not None
        assert frames[1].context_id == ctx_id
        assert frames[2].context_id == ctx_id

    @pytest.mark.asyncio
    async def test_none_message_type_returns_false(self, mock_engine):
        """messageType='none' returns False without queuing frames."""
        manager = CustomToolManager(mock_engine)
        result = await manager._play_config_message({"messageType": "none"})

        assert result is False
        assert len(mock_engine._queued_frames) == 0

    @pytest.mark.asyncio
    async def test_audio_without_fetch_callback_returns_false(self, mock_engine):
        """Audio without fetch_recording_audio callback returns False."""
        mock_engine._fetch_recording_audio = None

        manager = CustomToolManager(mock_engine)
        config = {"messageType": "audio", "audioRecordingId": "301"}

        result = await manager._play_config_message(config)

        assert result is False
        assert len(mock_engine._queued_frames) == 0

    @pytest.mark.asyncio
    async def test_audio_with_failed_fetch_returns_false(self, mock_engine):
        """Audio with fetch returning None returns False."""
        mock_fetch = AsyncMock(return_value=None)
        mock_engine._fetch_recording_audio = mock_fetch

        manager = CustomToolManager(mock_engine)
        config = {"messageType": "audio", "audioRecordingId": "301"}

        result = await manager._play_config_message(config)

        assert result is False
        mock_fetch.assert_called_once_with(recording_pk=301)
        assert len(mock_engine._queued_frames) == 0

    @pytest.mark.asyncio
    async def test_custom_empty_message_returns_false(self, mock_engine):
        """messageType='custom' with empty message returns False."""
        manager = CustomToolManager(mock_engine)
        config = {"messageType": "custom", "customMessage": ""}

        result = await manager._play_config_message(config)

        assert result is False
        assert len(mock_engine._queued_frames) == 0
