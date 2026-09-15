"""In-call agent transfer: one agent hands the live caller to another.

Everything here runs the real split pipeline -- a call worker holding the
transport, recognition, recording and the shared conversation, with a bridged
child worker per agent visit -- against pipecat's own worker runner and bus.
The invariants worth protecting are the ones a unit test cannot see:

* the call does not drop, and the transport keeps the same identity;
* caller audio keeps reaching the recorder across the handoff;
* the destination speaks with its own voice and its own tools;
* exactly one opening runs per activation;
* the previous agent is released without ending the call.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import (
    InputAudioRawFrame,
    MetricsFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_mute import CallbackUserMuteStrategy
from pipecat.utils.enums import EndTaskReason

from api.enums import ToolCategory
from api.services.pipecat.agent_bridge import (
    CALL_FRAMES,
    AgentBridgeProcessor,
)
from api.services.pipecat.agent_runtime_factory import (
    AgentBuildError,
    AgentGenerationCallbacks,
    AgentRuntimeFactory,
)
from api.services.pipecat.pipeline_builder import build_pipeline
from api.services.pipecat.pipeline_metrics_aggregator import (
    PipelineMetricsAggregator,
)
from api.services.pipecat.termination_funnel_processor import (
    TerminationFunnelProcessor,
)
from api.services.pipecat.worker_runner import create_worker_runner, run_worker_runner
from api.services.tool_management import (
    ToolManagementError,
    validate_tool_references,
)
from api.services.workflow.agent_runtime import AgentRuntime, new_visit_id
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
from api.tests.pipecat_test_utils import stub_agent_runtime
from pipecat.tests import MockLLMService, MockTTSService

TRANSFER_TOOL_UUID = "tool-transfer-agent"
DESTINATION_WORKFLOW_ID = 99


# --------------------------------------------------------------------------
# Fixtures for the two agents
# --------------------------------------------------------------------------


def build_agent_workflow(
    *,
    name: str,
    greeting: str | None,
    tool_uuids: list[str] | None = None,
) -> WorkflowGraph:
    """A two-node workflow: a start node that may greet, and an end node."""
    return WorkflowGraph(
        ReactFlowDTO(
            nodes=[
                RFNodeDTO(
                    id="start",
                    type="startCall",
                    position=Position(x=0, y=0),
                    data=StartCallNodeData(
                        name=f"{name} Start",
                        prompt=f"You are the {name} agent.",
                        is_start=True,
                        allow_interrupt=True,
                        add_global_prompt=False,
                        extraction_enabled=False,
                        greeting=greeting,
                        greeting_type="text" if greeting else None,
                        tool_uuids=tool_uuids or [],
                    ),
                ),
                RFNodeDTO(
                    id="end",
                    type="endCall",
                    position=Position(x=0, y=200),
                    data=EndCallNodeData(
                        name=f"{name} End",
                        prompt="Wrap up.",
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
                        condition="When the caller is done.",
                    ),
                ),
            ],
        )
    )


class TransferAgentTool:
    """Stands in for the persisted transfer_agent tool row."""

    def __init__(self, *, message: str | None = "Let me put you through to billing."):
        self.tool_uuid = TRANSFER_TOOL_UUID
        self.name = "Transfer to Billing"
        self.description = "Use when the caller asks about an invoice."
        self.category = ToolCategory.TRANSFER_AGENT.value
        self.definition = {
            "schema_version": 1,
            "type": "transfer_agent",
            "config": {
                "workflow_id": DESTINATION_WORKFLOW_ID,
                "message": message or "",
            },
        }


class RecordedAudioProbe:
    """Counts caller audio reaching the recorder's position in the pipeline."""

    def __init__(self, audio_buffer: AudioBufferProcessor):
        self.input_frames = 0
        original = audio_buffer.process_frame

        async def counting_process_frame(frame, direction):
            if isinstance(frame, InputAudioRawFrame):
                self.input_frames += 1
            await original(frame, direction)

        audio_buffer.process_frame = counting_process_frame


class SpeechProbe:
    """Records every line the caller is asked to be spoken, and by whom."""

    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def watch(self, worker: PipelineWorker, label: str) -> None:
        original = worker.queue_frame

        async def recording_queue_frame(frame, *args, **kwargs):
            if isinstance(frame, TTSSpeakFrame):
                self.lines.append((label, frame.text))
            await original(frame, *args, **kwargs)

        worker.queue_frame = recording_queue_frame

    def texts(self) -> list[str]:
        return [text for _, text in self.lines]


class TransferHarness:
    """A running call with a split pipeline and one or two agents."""

    def __init__(self):
        self.runner = create_worker_runner()
        self.transport: MockTransport | None = None
        self.engine: PipecatEngine | None = None
        self.call_worker: PipelineWorker | None = None
        self.audio_probe: RecordedAudioProbe | None = None
        self.speech = SpeechProbe()
        self.destination_llm: MockLLMService | None = None
        self.destination_tts: MockTTSService | None = None
        self.built_destinations: list[int] = []
        self.metrics_frames = 0
        self._run_task: asyncio.Task | None = None

    def _watch_metrics(self, aggregator: PipelineMetricsAggregator) -> None:
        original = aggregator.process_frame

        async def counting_process_frame(frame, direction):
            if isinstance(frame, MetricsFrame):
                self.metrics_frames += 1
            await original(frame, direction)

        aggregator.process_frame = counting_process_frame

    async def build(
        self,
        *,
        source_llm: MockLLMService,
        destination_llm: MockLLMService,
        source_workflow: WorkflowGraph,
        destination_workflow: WorkflowGraph,
        destination_build_error: AgentBuildError | None = None,
    ) -> None:
        call_worker_name = "call-test"
        self.destination_llm = destination_llm

        self.transport = MockTransport(
            generate_audio=True,
            audio_interval_ms=20,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=16000,
                audio_out_sample_rate=16000,
                audio_out_end_silence_secs=0,
            ),
        )
        audio_buffer = AudioBufferProcessor(sample_rate=16000)
        self.audio_probe = RecordedAudioProbe(audio_buffer)
        context = LLMContext()

        metrics_aggregator = PipelineMetricsAggregator()
        self._watch_metrics(metrics_aggregator)

        self.engine = PipecatEngine(
            llm=source_llm,
            context=context,
            workflow=source_workflow,
            call_context_vars={},
            workflow_run_id=1,
        )

        aggregators = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                user_mute_strategies=[
                    CallbackUserMuteStrategy(
                        should_mute_callback=self.engine.should_mute_user
                    )
                ],
            ),
        )

        generation_segment = [
            AgentBridgeProcessor(
                bus=self.runner.bus,
                worker_name=call_worker_name,
                selected_visit=lambda: self.engine.selected_visit_id,
                allow_inference=lambda: not self.engine.transfer_in_progress,
                name=f"{call_worker_name}::AgentBridge",
            ),
        ]
        pipeline = build_pipeline(
            self.transport,
            None,
            audio_buffer,
            aggregators.user(),
            aggregators.assistant(),
            None,
            generation_segment,
            metrics_aggregator,
            TerminationFunnelProcessor(),
        )
        self.call_worker = PipelineWorker(
            pipeline,
            name=call_worker_name,
            params=PipelineParams(audio_out_sample_rate=16000),
            enable_rtvi=False,
            idle_timeout_secs=None,
            # Production traces every call; without it the call worker has no
            # TracingContext and the assertions about sharing it are vacuous.
            enable_tracing=True,
            conversation_id="test-call",
        )
        self.engine.call_worker = self.call_worker
        self.engine.set_transport_output(self.transport.output())
        self.engine.set_audio_config(
            SimpleNamespace(
                pipeline_sample_rate=16000,
                transport_in_sample_rate=16000,
                transport_out_sample_rate=16000,
            )
        )
        self.speech.watch(self.call_worker, "call")

        factory = AgentRuntimeFactory(
            organization_id=1,
            workflow_run_id=1,
            call_worker=self.call_worker,
            audio_config=self.engine._audio_config,
            callbacks_factory=lambda visit_id: AgentGenerationCallbacks(
                generation_started=self.engine.create_generation_started_callback(
                    visit_id
                ),
                llm_text_frame=self.engine.create_llm_text_frame_callback(visit_id),
            ),
        )

        # Building a destination in a test resolves no workflow row; it wires
        # the caller-supplied services into the same worker the production
        # factory would build.
        original_attach = factory.attach

        async def fake_build(*, workflow_id: int, visit_id: str):
            self.built_destinations.append(workflow_id)
            if destination_build_error is not None:
                raise destination_build_error
            self.destination_tts = MockTTSService(
                mock_audio_duration_ms=20, frame_delay=0
            )
            runtime = AgentRuntime(
                workflow_id=workflow_id,
                definition_id=7,
                workflow_name="Billing",
                workflow=destination_workflow,
                llm=destination_llm,
                inference_llm=destination_llm,
                variable_extraction_llm=destination_llm,
                tts=self.destination_tts,
                recording_router=None,
                user_config=None,
                runtime_configuration={"llm_model": "billing-model"},
                is_realtime=False,
                is_child=True,
                visit_id=visit_id,
            )
            await original_attach(runtime)
            self.speech.watch(runtime.worker, "destination")
            return runtime

        factory.build = fake_build
        self.engine.set_agent_factory(factory)

        source_tts = MockTTSService(mock_audio_duration_ms=20, frame_delay=0)
        source = self.engine.active_agent
        source.workflow_id = 1
        source.definition_id = 1
        source.workflow_name = "Reception"
        source.tts = source_tts
        source.runtime_configuration = {"llm_model": "reception-model"}
        source.is_child = True
        source.worker = None

    async def start(self) -> None:
        self._run_task = asyncio.create_task(
            run_worker_runner(self.runner, self.call_worker)
        )
        await self._await_pipeline_started()
        assert await self.engine.start_initial_agent()
        self.speech.watch(self.engine.active_agent.worker, "source")

    async def _await_pipeline_started(self, timeout: float = 5.0) -> None:
        async with asyncio.timeout(timeout):
            while self.call_worker.started_at is None:
                await asyncio.sleep(0.01)

    async def stop(self) -> None:
        if self.engine and not self.engine.is_call_disposed():
            await self.engine.end_call_with_reason(EndTaskReason.END_CALL.value)
        if self._run_task:
            try:
                await asyncio.wait_for(self._run_task, timeout=10)
            except asyncio.TimeoutError:
                self._run_task.cancel()
                await asyncio.gather(self._run_task, return_exceptions=True)


async def run_transfer(
    harness: TransferHarness,
    *,
    tool: TransferAgentTool,
    timeout: float = 20.0,
) -> None:
    """Drive one handoff from the source agent's start node and wait for it."""
    engine = harness.engine
    with patch(
        "api.services.workflow.pipecat_engine_custom_tools.db_client.get_tools_by_uuids",
        new_callable=AsyncMock,
        return_value=[tool],
    ):
        engine._custom_tool_manager = CustomToolManager(engine)
        engine._get_organization_id = AsyncMock(return_value=1)
        await engine.set_node("start")
        await engine.queue_node_opening(
            node_id="start", previous_node_id=None, generate_if_no_greeting=True
        )

        coordinator = engine.transfer_coordinator
        async with asyncio.timeout(timeout):
            while not coordinator.completed:
                await asyncio.sleep(0.02)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_hands_the_call_over_without_dropping_it():
    """A → B: the caller stays connected and hears both agents in turn."""
    source_llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_function_call_chunks(
                "transfer_to_billing", {}, tool_call_id="call_transfer_1"
            )
        ],
        chunk_delay=0.001,
    )
    destination_llm = MockLLMService(
        mock_steps=[MockLLMService.create_text_chunks("Anything else?")],
        chunk_delay=0.001,
    )

    harness = TransferHarness()
    await harness.build(
        source_llm=source_llm,
        destination_llm=destination_llm,
        source_workflow=build_agent_workflow(
            name="Reception", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(
            name="Billing", greeting="Billing here, how can I help?"
        ),
    )
    await harness.start()
    source_visit = harness.engine.active_agent.visit_id
    audio_before = harness.audio_probe.input_frames

    try:
        await run_transfer(harness, tool=TransferAgentTool())

        engine = harness.engine
        outcome = engine.transfer_coordinator.completed[-1]
        assert outcome["outcome"] == "completed", outcome
        assert harness.built_destinations == [DESTINATION_WORKFLOW_ID]

        # The call itself never went anywhere.
        assert not harness.call_worker.has_finished()
        assert not engine.is_call_disposed()

        # The destination owns the call now, as a new visit.
        assert engine.active_agent.visit_id != source_visit
        assert engine.active_agent.workflow_id == DESTINATION_WORKFLOW_ID
        assert engine.active_agent.worker.active

        # The announcement came from the source agent and the greeting from
        # the destination -- each in its own voice, not through the transport.
        spoken = harness.speech.lines
        assert ("source", "Let me put you through to billing.") in spoken
        assert ("destination", "Billing here, how can I help?") in spoken

        # Caller audio kept reaching the recorder's position across the
        # handoff. It is excluded from the bus precisely so that consuming
        # bridge cannot swallow it.
        assert harness.audio_probe.input_frames > audio_before
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_transfer_compacts_the_conversation_for_the_destination():
    """The destination reads a handover note, not the previous agent's tools."""
    source_llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_function_call_chunks(
                "transfer_to_billing", {}, tool_call_id="call_transfer_1"
            )
        ],
        chunk_delay=0.001,
    )
    destination_llm = MockLLMService(
        mock_steps=[MockLLMService.create_text_chunks("Sure.")], chunk_delay=0.001
    )

    harness = TransferHarness()
    await harness.build(
        source_llm=source_llm,
        destination_llm=destination_llm,
        source_workflow=build_agent_workflow(
            name="Reception", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(name="Billing", greeting="Billing."),
    )
    await harness.start()

    # Conversation the source agent had before the handoff.
    harness.engine.context.set_messages(
        [
            {"role": "user", "content": "My invoice 4417 looks wrong."},
            {"role": "assistant", "content": "Let me check who can help."},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_lookup",
                        "type": "function",
                        "function": {"name": "lookup_account", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "content": "{}", "tool_call_id": "call_lookup"},
        ]
    )

    try:
        await run_transfer(harness, tool=TransferAgentTool())

        messages = harness.engine.context.messages
        # Nothing describing the previous agent's tool work survives: a
        # destination that does not publish those functions is handed
        # assistant tool calls and orphaned results it cannot answer for.
        assert not any(m.get("tool_calls") for m in messages if isinstance(m, dict))
        assert not any(m.get("role") == "tool" for m in messages if isinstance(m, dict))
        # What the caller actually said is carried over.
        assert any(
            "4417" in str(m.get("content", "")) for m in messages if isinstance(m, dict)
        )
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_a_destination_that_cannot_be_built_resumes_the_source_agent():
    """An unavailable destination is a failed transfer, not a failed call."""
    source_llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_function_call_chunks(
                "transfer_to_billing", {}, tool_call_id="call_transfer_1"
            ),
            MockLLMService.create_text_chunks("Sorry about that, let me help."),
        ],
        chunk_delay=0.001,
    )

    harness = TransferHarness()
    await harness.build(
        source_llm=source_llm,
        destination_llm=MockLLMService(mock_steps=[], chunk_delay=0.001),
        source_workflow=build_agent_workflow(
            name="Reception", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(name="Billing", greeting="Billing."),
        destination_build_error=AgentBuildError(
            "destination_not_published", "no published definition"
        ),
    )
    await harness.start()
    source_visit = harness.engine.active_agent.visit_id

    try:
        await run_transfer(harness, tool=TransferAgentTool())

        engine = harness.engine
        outcome = engine.transfer_coordinator.completed[-1]
        assert outcome["outcome"] == "destination_not_published"

        # The call is live, on the same agent, with its conversation intact.
        assert not harness.call_worker.has_finished()
        assert not engine.is_call_disposed()
        assert engine.active_agent.visit_id == source_visit
        assert engine.active_agent.worker.active
    finally:
        await harness.stop()


@pytest.mark.parametrize(
    "destination, expected_error",
    [
        (SimpleNamespace(id=DESTINATION_WORKFLOW_ID), None),
        (None, "destination_not_found"),
    ],
    ids=["own_agent_saves", "other_orgs_agent_refused"],
)
@pytest.mark.asyncio
async def test_a_transfer_tool_can_only_name_an_agent_in_its_own_organization(
    destination, expected_error
):
    """The destination is checked where it is configured, not mid-call.

    A workflow id in a request body proves the row exists and nothing about
    who owns it. Left to the call to discover, a destination belonging to
    another organization is indistinguishable from a working one until a
    caller is already on the line.
    """
    definition = TransferAgentTool().definition

    with patch(
        "api.services.tool_management.db_client.get_workflow",
        new_callable=AsyncMock,
        return_value=destination,
    ) as get_workflow:
        if expected_error is None:
            await validate_tool_references(definition, organization_id=7)
        else:
            with pytest.raises(ToolManagementError) as raised:
                await validate_tool_references(definition, organization_id=7)
            assert raised.value.error_code == expected_error
            assert raised.value.status_code == 404

    assert get_workflow.await_args.kwargs["organization_id"] == 7


@pytest.mark.asyncio
async def test_a_handover_that_fails_to_activate_leaves_the_conversation_alone():
    """The commit is a transaction: what it replaces, it puts back.

    Installing the destination rewrites the call's shared conversation --
    compacted history, its own tool schemas -- and only then activates it. A
    source agent resumed on top of those would answer the caller from a
    summary of its own call while holding another agent's tools.
    """
    source_llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_function_call_chunks(
                "transfer_to_billing", {}, tool_call_id="call_transfer_1"
            ),
            MockLLMService.create_text_chunks("Sorry about that, let me help."),
        ],
        chunk_delay=0.001,
    )

    harness = TransferHarness()
    await harness.build(
        source_llm=source_llm,
        destination_llm=MockLLMService(mock_steps=[], chunk_delay=0.001),
        source_workflow=build_agent_workflow(
            name="Reception", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(name="Billing", greeting="Billing."),
    )
    await harness.start()
    engine = harness.engine
    source_visit = engine.active_agent.visit_id

    engine.context.set_messages(
        [
            {"role": "user", "content": "My invoice 4417 looks wrong."},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_lookup",
                        "type": "function",
                        "function": {"name": "lookup_account", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "content": "{}", "tool_call_id": "call_lookup"},
        ]
    )

    # The destination builds and starts; only its activation never lands,
    # which is the one failure that happens after the commit has begun.
    activate = engine.activate_agent

    async def refuse_the_destination(runtime, *, timeout):
        if runtime.visit_id == source_visit:
            return await activate(runtime, timeout=timeout)
        engine.context.add_message(
            {"role": "user", "content": "Caller spoke during activation"}
        )
        return False

    engine.activate_agent = refuse_the_destination

    try:
        await run_transfer(harness, tool=TransferAgentTool())

        outcome = engine.transfer_coordinator.completed[-1]
        assert outcome["outcome"] == "activation_failed"
        assert engine.active_agent.visit_id == source_visit
        assert any(
            m.get("content") == "Caller spoke during activation"
            for m in engine.context.messages
        )

        # Tool traffic is what a handover strips, so finding it still here is
        # the proof that the compacted history never replaced the real one.
        messages = engine.context.messages
        assert any(m.get("tool_calls") for m in messages if isinstance(m, dict))
        assert any(m.get("role") == "tool" for m in messages if isinstance(m, dict))
        assert any(
            "4417" in str(m.get("content", "")) for m in messages if isinstance(m, dict)
        )
        # And the source agent still publishes its own tools: Billing's start
        # node has no transfer of its own, so this one could only be the
        # source's.
        assert "transfer_to_billing" in [
            f.name for f in engine.context.tools.standard_tools
        ]
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_a_hangup_during_preparation_never_activates_the_destination():
    """Ending the call invalidates a handoff rather than racing it."""
    source_llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_function_call_chunks(
                "transfer_to_billing", {}, tool_call_id="call_transfer_1"
            )
        ],
        chunk_delay=0.001,
    )

    harness = TransferHarness()
    await harness.build(
        source_llm=source_llm,
        destination_llm=MockLLMService(mock_steps=[], chunk_delay=0.001),
        source_workflow=build_agent_workflow(
            name="Reception", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(name="Billing", greeting="Billing."),
    )
    await harness.start()
    source_visit = harness.engine.active_agent.visit_id
    engine = harness.engine

    tool = TransferAgentTool()
    with patch(
        "api.services.workflow.pipecat_engine_custom_tools.db_client.get_tools_by_uuids",
        new_callable=AsyncMock,
        return_value=[tool],
    ):
        engine._custom_tool_manager = CustomToolManager(engine)
        engine._get_organization_id = AsyncMock(return_value=1)
        await engine.set_node("start")
        await engine.queue_node_opening(
            node_id="start", previous_node_id=None, generate_if_no_greeting=True
        )

        # Wait until the handoff is under way, then hang up on it.
        async with asyncio.timeout(10):
            while not engine.transfer_in_progress:
                await asyncio.sleep(0.01)
        await engine.end_call_with_reason(EndTaskReason.USER_HANGUP.value)

    assert engine.is_call_disposed()
    # The destination never took the call: whatever the handoff had reached,
    # the agent that owned the call at hangup is the one recorded.
    assert engine.active_agent.visit_id == source_visit
    assert (
        DESTINATION_WORKFLOW_ID
        not in [v["workflow_id"] for v in engine.agent_visits if v["workflow_id"]]
        or not engine.active_agent.worker.active
    )

    if harness._run_task:
        try:
            await asyncio.wait_for(harness._run_task, timeout=10)
        except asyncio.TimeoutError:
            harness._run_task.cancel()
            await asyncio.gather(harness._run_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_second_transfer_is_refused_while_one_is_running():
    """Two agents must never be mid-handover on the same call."""
    from api.services.workflow.agent_transfer import (
        AgentTransferCoordinator,
        TransferRequest,
    )

    engine = SimpleNamespace(is_call_disposed=lambda: False)
    coordinator = AgentTransferCoordinator(engine)

    first = TransferRequest(
        destination_workflow_id=1,
        destination_label="Transfer to Billing",
        origin_visit_id=new_visit_id(),
    )
    second = TransferRequest(
        destination_workflow_id=2,
        destination_label="Transfer to Support",
        origin_visit_id=new_visit_id(),
    )

    engine.active_agent = SimpleNamespace(visit_id=first.origin_visit_id)
    assert coordinator.accept(first) is True
    assert coordinator.accept(second) is False


@pytest.mark.asyncio
async def test_the_caller_hears_a_ringer_that_stops_before_the_destination_speaks():
    """Hold audio covers the wait and is bounded when the handoff lands."""
    source_llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_function_call_chunks(
                "transfer_to_billing", {}, tool_call_id="call_transfer_1"
            )
        ],
        chunk_delay=0.001,
    )

    harness = TransferHarness()
    await harness.build(
        source_llm=source_llm,
        destination_llm=MockLLMService(
            mock_steps=[MockLLMService.create_text_chunks("Hi.")], chunk_delay=0.001
        ),
        source_workflow=build_agent_workflow(
            name="Reception", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(
            name="Billing", greeting="Billing here."
        ),
    )
    await harness.start()

    ringer_frames: list[int] = []
    original_queue = harness.transport.output().queue_frame

    async def counting_queue_frame(frame, *args, **kwargs):
        if isinstance(frame, OutputAudioRawFrame) and not isinstance(
            frame, TTSAudioRawFrame
        ):
            ringer_frames.append(len(frame.audio))
        await original_queue(frame, *args, **kwargs)

    harness.transport.output().queue_frame = counting_queue_frame

    try:
        await run_transfer(harness, tool=TransferAgentTool())

        assert harness.engine.transfer_coordinator.completed[-1]["outcome"] == (
            "completed"
        )
        assert ringer_frames, "the caller heard no hold audio while waiting"
        # Chunked so that stopping leaves at most one chunk already queued;
        # a whole clip would keep ringing over the destination's greeting.
        assert max(ringer_frames) <= 16000 * 2 * 0.25, (
            "hold audio is queued in chunks, not whole clips"
        )

        # The ringer producer is stopped and awaited before the destination is
        # asked to open.
        assert harness.engine.transfer_coordinator.phase.value == "idle"
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_the_destination_opens_with_its_own_greeting():
    """A caller handed to another agent hears that agent introduce itself."""
    source_llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_function_call_chunks(
                "transfer_to_billing", {}, tool_call_id="call_transfer_1"
            )
        ],
        chunk_delay=0.001,
    )

    harness = TransferHarness()
    await harness.build(
        source_llm=source_llm,
        destination_llm=MockLLMService(
            mock_steps=[MockLLMService.create_text_chunks("Carrying on.")],
            chunk_delay=0.001,
        ),
        source_workflow=build_agent_workflow(
            name="Reception", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(
            name="Billing", greeting="Billing here, how can I help?"
        ),
    )
    await harness.start()

    try:
        await run_transfer(harness, tool=TransferAgentTool())

        assert harness.engine.transfer_coordinator.completed[-1]["outcome"] == (
            "completed"
        )
        # The destination always introduces itself with its own greeting.
        assert "Billing here, how can I help?" in harness.speech.texts()
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_usage_from_both_agents_reaches_the_call_and_the_run_records_both_visits():
    """Metrics from a child cross the bridge; the run knows every visit."""
    source_llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_function_call_chunks(
                "transfer_to_billing", {}, tool_call_id="call_transfer_1"
            )
        ],
        chunk_delay=0.001,
    )

    harness = TransferHarness()
    await harness.build(
        source_llm=source_llm,
        destination_llm=MockLLMService(
            mock_steps=[MockLLMService.create_text_chunks("Billing speaking.")],
            chunk_delay=0.001,
        ),
        source_workflow=build_agent_workflow(
            name="Reception", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(name="Billing", greeting="Hello."),
    )
    await harness.start()
    source_visit = harness.engine.active_agent.visit_id

    try:
        await run_transfer(harness, tool=TransferAgentTool())
        destination_visit = harness.engine.active_agent.visit_id

        # Metrics are produced inside an agent's own worker. They reach the
        # call's aggregator only because they cross the bridge, which is what
        # keeps one call's usage whole across a handoff.
        assert harness.metrics_frames > 0

        await harness.engine.end_call_with_reason(EndTaskReason.END_CALL.value)
        gathered = await harness.engine.get_gathered_context()

        visits = gathered["agent_visits"]
        assert [v["visit_id"] for v in visits] == [source_visit, destination_visit]
        assert visits[0]["exit_reason"] == "transferred"
        assert visits[1]["workflow_id"] == DESTINATION_WORKFLOW_ID
        assert visits[1]["runtime_configuration"]["llm_model"] == "billing-model"

        # The handoff is recorded on the run, not treated as its outcome: the
        # call went on with another agent, which produces the disposition.
        assert gathered["agent_transfers"][-1]["outcome"] == "completed"
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_an_announcement_that_never_plays_does_not_leave_the_caller_muted():
    """The mute taken for transfer speech is released even if nothing is said."""
    engine = PipecatEngine(workflow=None, call_context_vars={}, workflow_run_id=1)
    engine._active_agent = stub_agent_runtime()

    await engine.queue_text_message("Connecting you now.", mute_user=True)
    assert await engine.should_mute_user(SimpleNamespace()) is True

    engine.clear_queued_speech_mute()
    assert await engine.should_mute_user(SimpleNamespace()) is False


@pytest.mark.asyncio
async def test_the_call_worker_keeps_its_own_liveness_and_control_frames():
    """Frames addressed to the call worker must not be handed to the bus.

    A `HeartbeatFrame` is queued by the call worker and has to reach the call
    worker's sink; a `CancelWorkerFrame` is how the output transport ends a
    call whose audio writes keep failing, and the termination funnel that
    catches it sits upstream of the bridge. Published to the bus, both are
    lost: the first shows up as "heartbeat frame not received" during every
    hold, the second as a call that never ends.
    """
    from pipecat.frames.frames import (
        CancelWorkerFrame,
        EndWorkerFrame,
        HeartbeatFrame,
        InterruptionWorkerFrame,
        StopWorkerFrame,
    )

    for frame_type in (
        HeartbeatFrame,
        CancelWorkerFrame,
        EndWorkerFrame,
        StopWorkerFrame,
        InterruptionWorkerFrame,
        InputAudioRawFrame,
    ):
        assert frame_type in CALL_FRAMES, (
            f"{frame_type.__name__} would be published to the bus and lost"
        )


@pytest.mark.asyncio
async def test_agent_generations_are_traced_into_the_calls_turns():
    """An agent's LLM and TTS record spans against the call's conversation.

    A worker only builds a `TracingContext` when it also tracks turns of its
    own, which a child must not do. Without the call's context injected, every
    service in every agent worker runs untraced and the call's turns come out
    empty -- the trace looks fine until you open a turn.
    """
    harness = TransferHarness()
    await harness.build(
        source_llm=MockLLMService(mock_steps=[], chunk_delay=0.001),
        destination_llm=MockLLMService(mock_steps=[], chunk_delay=0.001),
        source_workflow=build_agent_workflow(
            name="Reception", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(name="Billing", greeting="Hi."),
    )
    # The call worker is the one that owns turn tracking and the trace.
    assert harness.call_worker.turn_tracking_observer is not None
    assert harness.call_worker._tracing_context is not None

    await harness.start()
    try:
        agent = harness.engine.active_agent
        assert agent.worker.turn_tracking_observer is None, (
            "an agent worker tracking its own turns would double-count them"
        )
        assert agent.worker._tracing_context is harness.call_worker._tracing_context, (
            "the agent's services resolve their parent span through this"
        )
        # What the `@traced_llm`/`@traced_tts` decorators actually gate on.
        # They read this before they look at any context, so a child holding
        # the call's context while its services have tracing off still emits
        # nothing.
        for service in (agent.llm, agent.tts):
            assert service._tracing_enabled, (
                f"{type(service).__name__} would skip tracing entirely"
            )
    finally:
        await harness.stop()


def _definition(definition_id: int, status: str, version: int):
    return SimpleNamespace(
        id=definition_id,
        status=status,
        version_number=version,
        workflow_json={"nodes": [], "edges": []},
        workflow_configurations={},
    )


def _destination_factory(*, use_draft: bool) -> AgentRuntimeFactory:
    """A factory with nothing wired but destination resolution."""
    return AgentRuntimeFactory(
        organization_id=7,
        workflow_run_id=1,
        call_worker=None,
        audio_config=None,
        callbacks_factory=lambda visit_id: None,
        use_draft=use_draft,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "use_draft, draft, expected_id",
    [
        # A production call transfers to the destination's published version
        # even when that agent has unpublished edits sitting in a draft.
        (False, _definition(999, "draft", 5), 998),
        # A test call is testing drafts, so the handover keeps testing drafts.
        (True, _definition(999, "draft", 5), 999),
        # A destination with nothing in progress is not a failed transfer.
        (True, None, 998),
    ],
    ids=["production_takes_published", "test_takes_draft", "test_without_draft"],
)
async def test_destination_version_follows_the_call(use_draft, draft, expected_id):
    """The destination runs the same kind of version the call is running.

    A test call that runs the first agent's draft and then hands over to
    another agent's last published version reads as a bug in the destination
    agent: nothing the caller or the author can see says a different version
    answered.
    """
    published = _definition(998, "published", 4)
    workflow = SimpleNamespace(
        id=251,
        name="Billing",
        organization_id=7,
        released_definition=published,
        current_definition=published,
    )
    factory = _destination_factory(use_draft=use_draft)

    with patch(
        "api.services.pipecat.agent_runtime_factory.db_client",
        SimpleNamespace(
            get_workflow=AsyncMock(return_value=workflow),
            get_draft_version=AsyncMock(return_value=draft),
        ),
    ) as fake_db:
        resolved_workflow, definition = await factory.resolve_destination(251)

    assert resolved_workflow is workflow
    assert definition.id == expected_id
    # Tenant scoping is not optional on this path: the workflow id comes from
    # a tool configuration, which proves nothing about who owns it.
    assert fake_db.get_workflow.await_args.kwargs["organization_id"] == 7


@pytest.mark.asyncio
async def test_inactive_source_output_is_dropped_but_usage_is_collected():
    from pipecat.metrics.metrics import TTSUsageMetricsData

    harness = TransferHarness()
    await harness.build(
        source_llm=MockLLMService(mock_steps=[]),
        destination_llm=MockLLMService(mock_steps=[]),
        source_workflow=build_agent_workflow(name="Source", greeting=None),
        destination_workflow=build_agent_workflow(name="Destination", greeting="Hello"),
    )
    await harness.start()
    engine = harness.engine
    source = engine.active_agent
    audio = []
    usage_seen = asyncio.Event()
    process = harness.transport.output().process_frame

    async def observe(frame, direction):
        if isinstance(frame, TTSAudioRawFrame):
            audio.append(frame)
        if isinstance(frame, MetricsFrame):
            usage_seen.set()
        await process(frame, direction)

    harness.transport.output().process_frame = observe
    try:
        await engine.deactivate_agent(source)
        destination = await engine.build_agent(workflow_id=99, visit_id=new_visit_id())
        assert await destination.wait_until_started()
        assert await engine.activate_agent(destination, timeout=2)
        # A provider may finish producing after deactivation. The candidate is
        # also active now, but neither the call nor the other child may hear A.
        await source.llm.push_frame(TTSAudioRawFrame(b"\0" * 640, 16000, 1))
        await source.llm.push_frame(
            MetricsFrame([TTSUsageMetricsData(processor="retiring-source", value=17)])
        )
        await asyncio.wait_for(usage_seen.wait(), 2)
        assert audio == []
        async with asyncio.timeout(2):
            while harness.metrics_frames == 0:
                await asyncio.sleep(0.001)
    finally:
        await engine.end_call_with_reason("user_hangup", abort_immediately=True)
        await harness.stop()


@pytest.mark.asyncio
async def test_preparation_timeout_closes_an_attached_candidate(monkeypatch):
    from api.services.workflow import agent_transfer

    harness = TransferHarness()
    await harness.build(
        source_llm=MockLLMService(
            mock_steps=[
                MockLLMService.create_function_call_chunks("transfer_to_billing", {})
            ]
        ),
        destination_llm=MockLLMService(mock_steps=[]),
        source_workflow=build_agent_workflow(
            name="Source", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(name="Destination", greeting="Hello"),
    )
    await harness.start()
    candidate = None

    async def stall(runtime):
        nonlocal candidate
        candidate = runtime
        await asyncio.Event().wait()

    monkeypatch.setattr(harness.engine, "prepare_agent", stall)
    monkeypatch.setattr(agent_transfer, "TRANSFER_PREPARE_TIMEOUT_SECONDS", 0.1)
    try:
        await run_transfer(harness, tool=TransferAgentTool(message=""))
        assert (
            harness.engine.transfer_coordinator.completed[-1]["outcome"]
            == "prepare_timeout"
        )
        assert candidate is not None and candidate.retired
        await asyncio.wait_for(candidate.worker.wait(), 2)
        assert harness.engine.pending_agent is None
        assert harness.engine.selected_visit_id == harness.engine.active_agent.visit_id
    finally:
        await harness.stop()


def test_call_greeting_override_belongs_only_to_the_initial_visit():
    from dataclasses import replace

    from api.services.workflow.pipecat_engine import GREETING_OVERRIDE_CONTEXT_KEY

    source = build_agent_workflow(name="Source", greeting="Source saved greeting")
    engine = PipecatEngine(
        workflow=source,
        call_context_vars={
            GREETING_OVERRIDE_CONTEXT_KEY: {"type": "text", "text": "Call override"},
        },
    )
    assert engine.get_start_greeting() == ("text", "Call override")
    destination = replace(
        engine.active_agent,
        visit_id=new_visit_id(),
        workflow=build_agent_workflow(
            name="Destination", greeting="Destination greeting"
        ),
        greeting_override=None,
    )
    engine.install_agent(destination, previous=engine.active_agent)
    assert engine.get_start_greeting() == ("text", "Destination greeting")


@pytest.mark.asyncio
async def test_compaction_reads_a_snapshot_and_keeps_history_on_failure():
    from api.services.workflow.agent_handoff_context import build_handoff_snapshot

    original = [{"role": "user", "content": f"Fact {i}"} for i in range(14)]
    context = LLMContext(messages=original)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def summarize(request):
        started.set()
        await finish.wait()
        assert request.context.messages[0]["content"] == "Fact 0"
        assert len(request.context.messages) == 14
        raise RuntimeError("provider unavailable")

    task = asyncio.create_task(
        build_handoff_snapshot(
            context,
            SimpleNamespace(_generate_summary=summarize),
            request_id="snapshot-test",
        )
    )
    await asyncio.wait_for(started.wait(), 2)
    context.messages[0]["content"] = "Edited live context"
    context.add_message({"role": "user", "content": "Caller on hold"})
    finish.set()
    snapshot = await asyncio.wait_for(task, 2)
    assert snapshot.boundary == 14
    assert len(snapshot.messages) == 14
    assert snapshot.messages[0]["content"] == "Fact 0"
    assert snapshot.summarized is False


@pytest.mark.asyncio
async def test_retired_tools_keep_their_origin_and_cannot_control_the_new_agent():
    engine = PipecatEngine(
        workflow=build_agent_workflow(name="Source", greeting=None),
        call_context_vars={},
    )
    source = engine.active_agent
    handler = AsyncMock()
    bound = source.bind_tool(engine, handler)
    engine._active_agent = stub_agent_runtime(visit_id="replacement")
    await bound(SimpleNamespace())
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_destination_mcp_session_is_prepared_and_closed_by_its_owner(monkeypatch):
    from api.db import db_client
    from api.services.workflow.agent_transfer import TransferRequest
    from api.tests.support.mcp_mock_server import running_mcp_server

    async with running_mcp_server() as url:
        tool = SimpleNamespace(
            tool_uuid="destination-mcp",
            name="Destination MCP",
            category="mcp",
            definition={
                "type": "mcp",
                "config": {
                    "transport": "streamable_http",
                    "url": url,
                },
            },
        )
        monkeypatch.setattr(
            db_client, "get_tools_by_uuids", AsyncMock(return_value=[tool])
        )
        harness = TransferHarness()
        await harness.build(
            source_llm=MockLLMService(mock_steps=[]),
            destination_llm=MockLLMService(mock_steps=[]),
            source_workflow=build_agent_workflow(name="Source", greeting=None),
            destination_workflow=build_agent_workflow(
                name="Destination", greeting="Hello", tool_uuids=[tool.tool_uuid]
            ),
        )
        engine = harness.engine
        engine._get_organization_id = AsyncMock(return_value=1)
        await harness.start()
        coordinator = engine.transfer_coordinator
        request = TransferRequest(
            destination_workflow_id=99,
            destination_label="Destination",
            origin_visit_id=engine.active_agent.visit_id,
        )
        try:
            assert coordinator.accept(request)
            coordinator.start(request)
            await asyncio.wait_for(coordinator._task, 10)
            assert coordinator.completed[-1]["outcome"] == "completed"
            session = engine.active_agent.mcp_sessions[tool.tool_uuid]
            assert session.available
            assert "mcp__destination_mcp__echo" in [
                f.name for f in engine.context.tools.standard_tools
            ]
            assert "echo:hello" in await session.call(
                "mcp__destination_mcp__echo", {"text": "hello"}
            )
        finally:
            await harness.stop()
        assert session._owner_task is None
        assert session._client is None


@pytest.mark.asyncio
async def test_minimum_ring_is_measured_after_the_announcement(monkeypatch):
    from api.services.workflow import agent_transfer

    harness = TransferHarness()
    await harness.build(
        source_llm=MockLLMService(mock_steps=[]),
        destination_llm=MockLLMService(mock_steps=[]),
        source_workflow=build_agent_workflow(name="Source", greeting=None),
        destination_workflow=build_agent_workflow(name="Destination", greeting="Hello"),
    )
    await harness.start()
    coordinator = harness.engine.transfer_coordinator
    ring_times = []

    async def announce(*args):
        await asyncio.sleep(0.1)

    async def ring(*, stop_event, **kwargs):
        ring_times.append(asyncio.get_running_loop().time())
        await stop_event.wait()
        ring_times.append(asyncio.get_running_loop().time())

    monkeypatch.setattr(coordinator, "_announce", announce)
    monkeypatch.setattr(agent_transfer, "play_hold_audio_loop", ring)
    monkeypatch.setattr(agent_transfer, "TRANSFER_MIN_HOLD_SECONDS", 0.05)
    request = agent_transfer.TransferRequest(
        destination_workflow_id=99,
        destination_label="Destination",
        origin_visit_id=harness.engine.active_agent.visit_id,
    )
    try:
        assert coordinator.accept(request)
        coordinator.start(request)
        await asyncio.wait_for(coordinator._task, 5)
        assert coordinator.completed[-1]["outcome"] == "completed"
        assert ring_times[1] - ring_times[0] >= 0.045
    finally:
        await harness.stop()


@pytest.mark.asyncio
async def test_call_shutdown_survives_cancellation_of_the_requesting_task():
    harness = TransferHarness()
    await harness.build(
        source_llm=MockLLMService(mock_steps=[]),
        destination_llm=MockLLMService(mock_steps=[]),
        source_workflow=build_agent_workflow(name="Source", greeting=None),
        destination_workflow=build_agent_workflow(name="Destination", greeting="Hello"),
    )
    await harness.start()
    closing = asyncio.Event()
    release = asyncio.Event()

    async def close_session():
        closing.set()
        await release.wait()

    engine = harness.engine
    engine.active_agent.mcp_sessions["slow-close"] = SimpleNamespace(
        close_managed=close_session
    )
    request = asyncio.create_task(engine.end_call_with_reason("end_call"))
    try:
        await asyncio.wait_for(closing.wait(), 2)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        release.set()
        await asyncio.wait_for(engine._shutdown_task, 2)
        await asyncio.wait_for(harness.call_worker.wait(), 2)
        assert engine.is_call_disposed()
    finally:
        release.set()
        await harness.stop()
