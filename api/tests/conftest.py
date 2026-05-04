"""
Shared mock fixtures and workflow helpers for unit tests.

Database setup (test DB creation, migrations, session isolation) lives in
the root api/conftest.py. This module provides lightweight, non-DB fixtures:
- Mock objects (engine, workflow model, workflow run, user config, tools)
- Pre-built WorkflowGraph fixtures for various node topologies
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.services.workflow.dto import (
    AgentNodeData,
    AgentRFNode,
    EdgeDataDTO,
    EndCallNodeData,
    EndCallRFNode,
    ExtractionVariableDTO,
    Position,
    ReactFlowDTO,
    RFEdgeDTO,
    StartCallNodeData,
    StartCallRFNode,
    VariableType,
)
from api.services.workflow.workflow import WorkflowGraph

START_CALL_SYSTEM_PROMPT = "Start Call System Prompt"
AGENT_SYSTEM_PROMPT = "Agent Node System Prompt"
END_CALL_SYSTEM_PROMPT = "End Call System Prompt"

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


@dataclass
class MockToolModel:
    """Mock tool model for testing."""

    tool_uuid: str
    name: str
    description: str
    definition: Dict[str, Any]
    category: str = "http_api"


@pytest.fixture
def mock_engine():
    """Create a mock PipecatEngine.

    Binds the real `_get_organization_id` method so the fetch + cache logic
    runs against a patched `db_client.get_organization_id_by_workflow_run_id`
    (returns org_id=1) for the duration of the fixture.
    """
    from api.services.workflow.pipecat_engine import PipecatEngine

    engine = Mock()
    engine._workflow_run_id = 1
    engine._call_context_vars = {"customer_name": "John Doe"}
    engine._organization_id = None
    engine._get_organization_id = PipecatEngine._get_organization_id.__get__(engine)
    engine.llm = Mock()
    engine.llm.register_function = Mock()

    with patch(
        "api.db:db_client.get_organization_id_by_workflow_run_id",
        new_callable=AsyncMock,
        return_value=1,
    ):
        yield engine


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
    - Start node with extraction enabled (extracts user_intent)
    - End node with a prompt
    - One edge connecting them with label "End Call"
    """
    dto = ReactFlowDTO(
        nodes=[
            StartCallRFNode(
                id="start",
                position=Position(x=0, y=0),
                data=StartCallNodeData(
                    name="Start Call",
                    prompt=START_CALL_SYSTEM_PROMPT,
                    is_start=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=True,
                    extraction_prompt="Extract user information from the conversation.",
                    extraction_variables=[
                        ExtractionVariableDTO(
                            name="user_intent",
                            type=VariableType.string,
                            prompt="The user's intent or reason for calling",
                        ),
                    ],
                ),
            ),
            EndCallRFNode(
                id="end",
                position=Position(x=0, y=200),
                data=EndCallNodeData(
                    name="End Call",
                    prompt=END_CALL_SYSTEM_PROMPT,
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
    - Start node with extraction enabled (extracts greeting_type)
    - Agent node with extraction enabled (extracts user_name)
    - End node (no extraction)

    Edges:
    - Start -> Agent (label: "Collect Info")
    - Agent -> End (label: "End Call")
    """
    dto = ReactFlowDTO(
        nodes=[
            StartCallRFNode(
                id="start",
                position=Position(x=0, y=0),
                data=StartCallNodeData(
                    name="Start Call",
                    prompt=START_CALL_SYSTEM_PROMPT,
                    is_start=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=True,
                    extraction_prompt="Extract greeting information from the conversation.",
                    extraction_variables=[
                        ExtractionVariableDTO(
                            name="greeting_type",
                            type=VariableType.string,
                            prompt="The type of greeting used",
                        ),
                    ],
                ),
            ),
            AgentRFNode(
                id="agent",
                position=Position(x=0, y=200),
                data=AgentNodeData(
                    name="Collect Info",
                    prompt=AGENT_SYSTEM_PROMPT,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=True,
                    extraction_prompt="Extract user details from the conversation.",
                    extraction_variables=[
                        ExtractionVariableDTO(
                            name="user_name",
                            type=VariableType.string,
                            prompt="The user's name",
                        ),
                    ],
                ),
            ),
            EndCallRFNode(
                id="end",
                position=Position(x=0, y=400),
                data=EndCallNodeData(
                    name="End Call",
                    prompt=END_CALL_SYSTEM_PROMPT,
                    is_end=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=False,
                ),
            ),
        ],
        edges=[
            RFEdgeDTO(
                id="start-agent",
                source="start",
                target="agent",
                data=EdgeDataDTO(
                    label="Collect Info",
                    condition="When user has been greeted, proceed to collect information",
                ),
            ),
            RFEdgeDTO(
                id="agent-end",
                source="agent",
                target="end",
                data=EdgeDataDTO(
                    label="End Call",
                    condition="When information collection is complete, end the call",
                ),
            ),
        ],
    )
    return WorkflowGraph(dto)


@pytest.fixture
def three_node_workflow_extraction_start_only() -> WorkflowGraph:
    """Create a three-node workflow with extraction enabled ONLY on start node.

    This fixture is specifically for testing that variable extraction is triggered
    for the correct node during transitions. The agent node has extraction disabled
    to verify extraction happens for the SOURCE node, not the TARGET node.

    The workflow has:
    - Start node with extraction enabled (extracts user_name)
    - Agent node with extraction DISABLED
    - End node (no extraction)
    """
    dto = ReactFlowDTO(
        nodes=[
            StartCallRFNode(
                id="start",
                position=Position(x=0, y=0),
                data=StartCallNodeData(
                    name="Start Call",
                    prompt=START_CALL_SYSTEM_PROMPT,
                    is_start=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=True,
                    extraction_prompt="Extract the user's name from the conversation.",
                    extraction_variables=[
                        ExtractionVariableDTO(
                            name="user_name",
                            type=VariableType.string,
                            prompt="The name the user provided",
                        ),
                    ],
                ),
            ),
            AgentRFNode(
                id="agent",
                position=Position(x=0, y=200),
                data=AgentNodeData(
                    name="Collect Info",
                    prompt=AGENT_SYSTEM_PROMPT,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=False,  # Explicitly disabled for testing
                ),
            ),
            EndCallRFNode(
                id="end",
                position=Position(x=0, y=400),
                data=EndCallNodeData(
                    name="End Call",
                    prompt=END_CALL_SYSTEM_PROMPT,
                    is_end=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=False,
                ),
            ),
        ],
        edges=[
            RFEdgeDTO(
                id="start-agent",
                source="start",
                target="agent",
                data=EdgeDataDTO(
                    label="Collect Info",
                    condition="When user has been greeted, proceed to collect information",
                ),
            ),
            RFEdgeDTO(
                id="agent-end",
                source="agent",
                target="end",
                data=EdgeDataDTO(
                    label="End Call",
                    condition="When information collection is complete, end the call",
                ),
            ),
        ],
    )
    return WorkflowGraph(dto)


@pytest.fixture
def three_node_workflow_no_variable_extraction() -> WorkflowGraph:
    """Create a three-node workflow without variable extraction

    The workflow has:
    - Start node with extraction DISABLED
    - Agent node with extraction DISABLED
    - End node (no extraction)
    """
    dto = ReactFlowDTO(
        nodes=[
            StartCallRFNode(
                id="start",
                position=Position(x=0, y=0),
                data=StartCallNodeData(
                    name="Start Call",
                    prompt=START_CALL_SYSTEM_PROMPT,
                    is_start=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=False,
                ),
            ),
            AgentRFNode(
                id="agent",
                position=Position(x=0, y=200),
                data=AgentNodeData(
                    name="Collect Info",
                    prompt=AGENT_SYSTEM_PROMPT,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=False,  # Explicitly disabled for testing
                ),
            ),
            EndCallRFNode(
                id="end",
                position=Position(x=0, y=400),
                data=EndCallNodeData(
                    name="End Call",
                    prompt=END_CALL_SYSTEM_PROMPT,
                    is_end=True,
                    allow_interrupt=False,
                    add_global_prompt=False,
                    extraction_enabled=False,
                ),
            ),
        ],
        edges=[
            RFEdgeDTO(
                id="start-agent",
                source="start",
                target="agent",
                data=EdgeDataDTO(
                    label="Collect Info",
                    condition="When user has been greeted, proceed to collect information",
                ),
            ),
            RFEdgeDTO(
                id="agent-end",
                source="agent",
                target="end",
                data=EdgeDataDTO(
                    label="End Call",
                    condition="When information collection is complete, end the call",
                ),
            ),
        ],
    )
    return WorkflowGraph(dto)
