from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from unittest.mock import Mock

import pytest

from api.services.workflow.dto import (
    EdgeDataDTO,
    NodeDataDTO,
    NodeType,
    Position,
    ReactFlowDTO,
    RFEdgeDTO,
    RFNodeDTO,
)
from api.services.workflow.workflow import WorkflowGraph
from pipecat.frames.frames import (
    BotSpeakingFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

START_CALL_SYSTEM_PROMPT = "start_call_system_prompt"
END_CALL_SYSTEM_PROMPT = "end_call_system_prompt"

# Default workflow definition for mocking database WorkflowModel
DEFAULT_WORKFLOW_DEFINITION = {
    "nodes": [
        {
            "id": "1",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Start",
                "prompt": START_CALL_SYSTEM_PROMPT,
                "is_start": True,
                "allow_interrupt": False,
                "add_global_prompt": False,
            },
        },
        {
            "id": "2",
            "type": "endCall",
            "position": {"x": 0, "y": 200},
            "data": {
                "name": "End",
                "prompt": END_CALL_SYSTEM_PROMPT,
                "is_end": True,
                "allow_interrupt": False,
                "add_global_prompt": False,
            },
        },
    ],
    "edges": [
        {
            "id": "1-2",
            "source": "1",
            "target": "2",
            "data": {"label": "End", "condition": "End the call"},
        }
    ],
}


@dataclass
class MockWorkflowModel:
    """Mock database WorkflowModel for testing.

    This mimics the structure of the database WorkflowModel, not the parsed WorkflowGraph.
    Use this when mocking db_client.get_workflow() responses.
    """

    workflow_id: int = 1
    organization_id: int = 1
    workflow_configurations: Dict[str, Any] = field(default_factory=dict)
    workflow_definition_with_fallback: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.workflow_definition_with_fallback:
            self.workflow_definition_with_fallback = DEFAULT_WORKFLOW_DEFINITION.copy()


@dataclass
class MockWorkflowRun:
    """Mock database WorkflowRun for testing.

    Use this when mocking db_client.get_workflow_run() responses.
    """

    is_completed: bool = False
    initial_context: Dict[str, Any] = field(default_factory=dict)
    gathered_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MockUserConfig:
    """Mock user configuration for testing.

    Use this when mocking db_client.get_user_configurations() responses.
    """

    stt: Optional[Any] = None
    tts: Optional[Any] = None
    llm: Optional[Any] = None
    embeddings: Optional[Any] = None


class MockTransportProcessor(FrameProcessor):
    """
    Mocks the transport behavior by emitting Bot speaking frames
    when it encounters TTS frames.

    This simulates what a real transport would do when the bot is speaking:
    - TTSStartedFrame -> BotStartedSpeakingFrame
    - TTSAudioRawFrame -> BotSpeakingFrame
    - TTSStoppedFrame -> BotStoppedSpeakingFrame

    Args:
        emit_bot_speaking: If True, also emits BotSpeakingFrame on TTSAudioRawFrame
            which is needed for user idle tracking to start conversation tracking. Default True.
    """

    def __init__(
        self,
        *,
        emit_bot_speaking: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._emit_bot_speaking = emit_bot_speaking

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSStartedFrame):
            # Emit BotStartedSpeakingFrame to indicate bot started speaking
            await self.push_frame(BotStartedSpeakingFrame())
            await self.push_frame(
                BotStartedSpeakingFrame(), direction=FrameDirection.UPSTREAM
            )
        elif isinstance(frame, TTSAudioRawFrame):
            # Emit BotSpeakingFrame - this is what triggers user idle tracking
            # to start conversation tracking
            if self._emit_bot_speaking:
                await self.push_frame(BotSpeakingFrame())
                await self.push_frame(
                    BotSpeakingFrame(), direction=FrameDirection.UPSTREAM
                )
        elif isinstance(frame, TTSStoppedFrame):
            # Emit BotStoppedSpeakingFrame to indicate bot stopped speaking
            await self.push_frame(BotStoppedSpeakingFrame())
            await self.push_frame(
                BotStoppedSpeakingFrame(), direction=FrameDirection.UPSTREAM
            )

        await self.push_frame(frame, direction)


@dataclass
class MockToolModel:
    """Mock tool model for testing."""

    tool_uuid: str
    name: str
    description: str
    definition: Dict[str, Any]


@pytest.fixture
def mock_engine():
    """Create a mock PipecatEngine."""
    engine = Mock()
    engine._workflow_run_id = 1
    engine._call_context_vars = {"customer_name": "John Doe"}
    engine.llm = Mock()
    engine.llm.register_function = Mock()
    return engine


@pytest.fixture
def mock_workflow_model():
    """Create a mock WorkflowModel for testing database responses."""
    return MockWorkflowModel()


@pytest.fixture
def mock_workflow_run():
    """Create a mock WorkflowRun for testing database responses."""
    return MockWorkflowRun()


@pytest.fixture
def mock_user_config():
    """Create a mock user configuration for testing."""
    return MockUserConfig()


@pytest.fixture
def sample_tools():
    """Create sample mock tools for testing."""
    return [
        MockToolModel(
            tool_uuid="weather-uuid-123",
            name="Get Weather",
            description="Get current weather for a location",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "GET",
                    "url": "https://api.weather.com/current",
                    "parameters": [
                        {
                            "name": "location",
                            "type": "string",
                            "description": "City name (e.g., San Francisco, CA)",
                            "required": True,
                        },
                        {
                            "name": "units",
                            "type": "string",
                            "description": "Temperature units: celsius or fahrenheit",
                            "required": False,
                        },
                    ],
                },
            },
        ),
        MockToolModel(
            tool_uuid="booking-uuid-456",
            name="Book Appointment",
            description="Book an appointment for the customer",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/appointments",
                    "parameters": [
                        {
                            "name": "customer_name",
                            "type": "string",
                            "description": "Customer's full name",
                            "required": True,
                        },
                        {
                            "name": "date",
                            "type": "string",
                            "description": "Appointment date (YYYY-MM-DD)",
                            "required": True,
                        },
                        {
                            "name": "time",
                            "type": "string",
                            "description": "Appointment time (HH:MM)",
                            "required": True,
                        },
                        {
                            "name": "notes",
                            "type": "string",
                            "description": "Additional notes",
                            "required": False,
                        },
                    ],
                },
            },
        ),
        MockToolModel(
            tool_uuid="lookup-uuid-789",
            name="Customer Lookup",
            description="Look up customer information by phone number",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "GET",
                    "url": "https://api.example.com/customers/lookup",
                    "parameters": [
                        {
                            "name": "phone",
                            "type": "string",
                            "description": "Customer phone number",
                            "required": True,
                        },
                    ],
                },
            },
        ),
    ]


@pytest.fixture
def simple_workflow() -> WorkflowGraph:
    """Create a simple two-node workflow for testing.

    The workflow has:
    - Start node with a prompt
    - End node with a prompt
    - One edge connecting them with label "End Call"
    """
    dto = ReactFlowDTO(
        nodes=[
            RFNodeDTO(
                id="1",
                type=NodeType.startNode,
                position=Position(x=0, y=0),
                data=NodeDataDTO(
                    name="Start Call",
                    prompt=START_CALL_SYSTEM_PROMPT,
                    is_start=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                ),
            ),
            RFNodeDTO(
                id="2",
                type=NodeType.endNode,
                position=Position(x=0, y=200),
                data=NodeDataDTO(
                    name="End Call",
                    prompt=END_CALL_SYSTEM_PROMPT,
                    is_end=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                ),
            ),
        ],
        edges=[
            RFEdgeDTO(
                id="1-2",
                source="1",
                target="2",
                data=EdgeDataDTO(
                    label="End Call",
                    condition="When the user says to end the call, end the call",
                ),
            ),
        ],
    )
    return WorkflowGraph(dto)


@pytest.fixture
def three_node_workflow() -> WorkflowGraph:
    """Create a three-node workflow for testing with an intermediate agent node.

    The workflow has:
    - Start node
    - Agent node (for collecting information)
    - End node
    """
    dto = ReactFlowDTO(
        nodes=[
            RFNodeDTO(
                id="1",
                type=NodeType.startNode,
                position=Position(x=0, y=0),
                data=NodeDataDTO(
                    name="Start Call",
                    prompt=START_CALL_SYSTEM_PROMPT,
                    is_start=True,
                    allow_interrupt=True,
                    add_global_prompt=False,
                ),
            ),
            RFNodeDTO(
                id="2",
                type=NodeType.agentNode,
                position=Position(x=0, y=200),
                data=NodeDataDTO(
                    name="Collect Info",
                    prompt="Help the user with their request. Ask clarifying questions if needed.",
                    allow_interrupt=True,
                    add_global_prompt=False,
                ),
            ),
            RFNodeDTO(
                id="3",
                type=NodeType.endNode,
                position=Position(x=0, y=400),
                data=NodeDataDTO(
                    name="End Call",
                    prompt=END_CALL_SYSTEM_PROMPT,
                    is_end=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                ),
            ),
        ],
        edges=[
            RFEdgeDTO(
                id="1-2",
                source="1",
                target="2",
                data=EdgeDataDTO(
                    label="Collect Info",
                    condition="When the user wants help, collect their information",
                ),
            ),
            RFEdgeDTO(
                id="2-3",
                source="2",
                target="3",
                data=EdgeDataDTO(
                    label="End Call",
                    condition="When the user is done or wants to end the call",
                ),
            ),
        ],
    )
    return WorkflowGraph(dto)
