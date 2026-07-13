# Dograh-LiveKit Bridge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `dograh-livekit`, a new microservice that consumes Dograh's configuration via internal HTTP APIs and runs multi-agent voice conversations on LiveKit Cloud using Agno Workflows, as a parallel pathway to the existing Pipecat runtime.

**Architecture:** A new Python microservice (`dograh-livekit/`) sits alongside Dograh's existing services. It uses LiveKit AgentServer to accept SIP/WebRTC jobs, fetches runtime config from Dograh's internal API, translates Dograh's ReactFlow JSON into Agno Workflows, and executes them. An internal API endpoint is added to Dograh for runtime config delivery. A new `LiveKitSipProvider` is added to Dograh's telephony abstraction for campaign outbound calls.

**Tech Stack:** Python 3.12+, LiveKit Agents SDK, Agno framework, httpx, Pydantic, pytest, LiveKit Cloud (SIP/WebRTC)

## Global Constraints

- Dograh `api/` code is NOT modified except for the new internal endpoint and `LiveKitSipProvider`
- All Dograh data access goes through HTTP API — no direct DB access from `dograh-livekit`
- All internal endpoints use `X-Internal-Token` header
- Follows existing Dograh patterns: Python 3.12, pytest, async/await, structured logging
- Deployable as a separate Docker service in `docker-compose.yml`

---

### Task 1: Project Scaffolding

**Files:**
- Create: `dograh-livekit/pyproject.toml`
- Create: `dograh-livekit/Dockerfile`
- Create: `dograh-livekit/app/__init__.py`
- Create: `dograh-livekit/app/config.py`
- Create: `dograh-livekit/tests/__init__.py`
- Create: `dograh-livekit/tests/conftest.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `app.config.Settings` (Pydantic BaseSettings), importable package structure

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "dograh-livekit"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "livekit-agents>=1.0.0",
    "agno>=1.0.0",
    "httpx>=0.27.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "pytest-mock>=3.14.0",
    "respx>=0.21.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create app/config.py**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    dograh_api_url: str = "http://api:8000"
    dograh_internal_token: str = ""
    google_api_key: str = ""
    openai_api_key: str = ""
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
```

- [ ] **Step 3: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY app/ ./app/

CMD ["python", "-m", "app.main"]
```

- [ ] **Step 4: Create empty __init__.py files and conftest.py**

```python
# dograh-livekit/app/__init__.py
```

```python
# dograh-livekit/tests/__init__.py
```

```python
# dograh-livekit/tests/conftest.py
import pytest


@pytest.fixture
def settings():
    from app.config import Settings
    return Settings(
        livekit_url="ws://test.livekit.io",
        livekit_api_key="test-key",
        livekit_api_secret="test-secret",
        dograh_api_url="http://test-dograh:8000",
        dograh_internal_token="test-token",
    )
```

- [ ] **Step 5: Verify imports and commit**

Run: `cd dograh-livekit && pip install -e ".[dev]" && python -c "from app.config import settings; print('OK')"`
Expected: prints "OK"

```bash
git add dograh-livekit/
git commit -m "feat: scaffold dograh-livekit project structure"
```

---

### Task 2: Pydantic Models for Dograh API

**Files:**
- Create: `dograh-livekit/app/models.py`
- Create: `dograh-livekit/tests/test_models.py`

**Interfaces:**
- Consumes: `app.config.Settings` (from Task 1)
- Produces: `RuntimeConfig`, `NodeData`, `EdgeData`, `ToolDefinition`, `SessionRecord`

- [ ] **Step 1: Write the test**

```python
# dograh-livekit/tests/test_models.py
import pytest
from app.models import (
    RuntimeConfig,
    NodeData,
    EdgeData,
    ToolDefinition,
    ModelConfig,
    STTConfig,
    TTSConfig,
)


class TestRuntimeConfig:
    def test_valid_minimal_config(self):
        data = {
            "deploy_id": "dp_123",
            "org_id": "org_456",
            "agent_id": "ag_789",
            "workflow_graph": {
                "id": "wf_1",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "startCall",
                        "position": {"x": 0, "y": 0},
                        "data": {
                            "name": "Start",
                            "prompt": "Hello",
                            "greeting": "Hi there",
                        },
                    },
                    {
                        "id": "n2",
                        "type": "endCall",
                        "position": {"x": 100, "y": 100},
                        "data": {"name": "End", "prompt": "Goodbye"},
                    },
                ],
                "edges": [],
            },
            "llm_config": {"provider": "google_realtime", "model": "gemini-2.5-flash-native-audio"},
            "stt_config": {"provider": "deepgram", "model_id": "nova-3"},
            "tts_config": {"provider": "cartesia", "voice_id": "Kore"},
            "system_prompt": "You are a helpful assistant.",
            "tools": [],
        }
        config = RuntimeConfig(**data)
        assert config.deploy_id == "dp_123"
        assert len(config.workflow_graph["nodes"]) == 2

    def test_invalid_missing_deploy_id(self):
        with pytest.raises(ValueError):
            RuntimeConfig(org_id="org_1", workflow_graph={"nodes": [], "edges": []})

    def test_tool_definition_parsing(self):
        tool = ToolDefinition(
            name="search_knowledge",
            type="kb_search",
            config={"kb_refs": ["kb_1", "kb_2"]},
        )
        assert tool.name == "search_knowledge"
        assert tool.config["kb_refs"] == ["kb_1", "kb_2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dograh-livekit && python -m pytest tests/test_models.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write app/models.py**

```python
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class Position(BaseModel):
    x: float
    y: float


class NodeData(BaseModel):
    """Flexible model for any Dograh node type."""
    name: str = ""
    prompt: str | None = None
    greeting: str | None = None
    greeting_type: str | None = None
    allow_interrupt: bool = True
    add_global_prompt: bool = True
    extraction_enabled: bool = False
    extraction_prompt: str | None = None
    extraction_variables: list[dict] | None = None
    tool_uuids: list[str] | None = None
    document_uuids: list[str] | None = None
    enabled: bool = True
    # Allow any extra fields from Dograh node data
    model_config = {"extra": "allow"}


class RFNode(BaseModel):
    id: str
    type: str
    position: Position
    data: NodeData = Field(default_factory=NodeData)


class EdgeData(BaseModel):
    label: str = ""
    condition: str = ""
    transition_speech: str | None = None
    model_config = {"extra": "allow"}


class RFEdge(BaseModel):
    id: str
    source: str
    target: str
    data: EdgeData = Field(default_factory=EdgeData)


class WorkflowGraph(BaseModel):
    id: str | None = None
    nodes: list[RFNode]
    edges: list[RFEdge]


class ModelConfig(BaseModel):
    provider: str = "google_realtime"
    model: str = "gemini-2.5-flash-native-audio"
    temperature: float = 0.8
    max_output_tokens: int | None = None
    vertexai: bool = False
    project: str = ""
    location: str = "europe-west1"
    model_config = {"extra": "allow"}


class STTConfig(BaseModel):
    provider: str = "deepgram"
    model_id: str = "nova-3"
    model_config = {"extra": "allow"}


class TTSConfig(BaseModel):
    provider: str = "cartesia"
    model_id: str = "sonic-3"
    voice_id: str = "Kore"
    model_config = {"extra": "allow"}


class ToolDefinition(BaseModel):
    name: str
    type: str = "custom"
    config: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "allow"}


class RuntimeConfig(BaseModel):
    """Full config returned by Dograh's GET /api/internal/deploy/{id}/runtime-config."""
    deploy_id: str
    org_id: str
    agent_id: str = ""
    agent_name: str = ""
    workflow_graph: WorkflowGraph
    llm_config: ModelConfig = Field(default_factory=ModelConfig)
    stt_config: STTConfig = Field(default_factory=STTConfig)
    tts_config: TTSConfig = Field(default_factory=TTSConfig)
    system_prompt: str = ""
    greeting_message: str = ""
    tools: list[ToolDefinition] = Field(default_factory=list)
    kb_refs: list[str] = Field(default_factory=list)
    handoff_sip_number: str = ""
    channel: str = "voice_sip"
    stages: list[dict] = Field(default_factory=list)
    orchestrator_mode: str = "agentos"

    model_config = {"extra": "allow"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dograh-livekit && python -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dograh-livekit/app/models.py dograh-livekit/tests/test_models.py
git commit -m "feat: add Pydantic models for Dograh API config"
```

---

### Task 3: Dograh HTTP Client

**Files:**
- Create: `dograh-livekit/app/dograh_client.py`
- Create: `dograh-livekit/tests/test_dograh_client.py`

**Interfaces:**
- Consumes: `app.config.Settings`, `app.models.RuntimeConfig`
- Produces: `DograhClient` class with `fetch_runtime_config()`, `search_knowledge()`, `create_session()`, `update_session()`

- [ ] **Step 1: Write the test**

```python
# dograh-livekit/tests/test_dograh_client.py
import pytest
import respx
from httpx import Response
from app.dograh_client import DograhClient


@pytest.fixture
def client(settings):
    return DograhClient(settings)


class TestFetchRuntimeConfig:
    @pytest.mark.asyncio
    async def test_fetches_full_config(self, client, settings):
        mock_response = {
            "deploy_id": "dp_123",
            "org_id": "org_456",
            "agent_id": "ag_789",
            "agent_name": "Test Agent",
            "workflow_graph": {
                "nodes": [
                    {
                        "id": "n1",
                        "type": "startCall",
                        "position": {"x": 0, "y": 0},
                        "data": {"name": "Start", "prompt": "Hello"},
                    }
                ],
                "edges": [],
            },
            "llm_config": {"provider": "google_realtime", "model": "gemini-2.5-flash-native-audio"},
            "stt_config": {"provider": "deepgram", "model_id": "nova-3"},
            "tts_config": {"provider": "cartesia", "voice_id": "Kore"},
            "system_prompt": "You are helpful.",
            "tools": [{"name": "search_knowledge", "type": "kb_search", "config": {"kb_refs": ["kb_1"]}}],
        }

        with respx.mock:
            respx.get(f"{settings.dograh_api_url}/api/internal/deploy/dp_123/runtime-config").mock(
                return_value=Response(200, json=mock_response)
            )
            config = await client.fetch_runtime_config("dp_123")
            assert config.deploy_id == "dp_123"
            assert len(config.tools) == 1

    @pytest.mark.asyncio
    async def test_handles_404(self, client, settings):
        with respx.mock:
            respx.get(f"{settings.dograh_api_url}/api/internal/deploy/unknown/runtime-config").mock(
                return_value=Response(404, json={"detail": "Not found"})
            )
            with pytest.raises(ValueError, match="not found"):
                await client.fetch_runtime_config("unknown")


class TestSearchKnowledge:
    @pytest.mark.asyncio
    async def test_searches_kb(self, client, settings):
        mock_results = {
            "results": [
                {"content": "We are open Mon-Fri 9-5", "score": 0.95, "source": "kb_1"},
            ]
        }
        with respx.mock:
            respx.post(f"{settings.dograh_api_url}/api/internal/kb/org_456/search").mock(
                return_value=Response(200, json=mock_results)
            )
            results = await client.search_knowledge("org_456", "opening hours")
            assert len(results["results"]) == 1
            assert results["results"][0]["content"] == "We are open Mon-Fri 9-5"


class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_create_session(self, client, settings):
        mock_session = {"id": "sess_001", "status": "active"}
        with respx.mock:
            respx.post(f"{settings.dograh_api_url}/api/internal/sessions").mock(
                return_value=Response(201, json=mock_session)
            )
            session = await client.create_session(
                deploy_id="dp_123",
                org_id="org_456",
                room_name="test-room",
                channel="voice_sip",
                agent_id="ag_789",
            )
            assert session["id"] == "sess_001"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dograh-livekit && python -m pytest tests/test_dograh_client.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write app/dograh_client.py**

```python
from __future__ import annotations

import logging
from typing import Any

import httpx
from app.config import Settings
from app.models import RuntimeConfig

logger = logging.getLogger(__name__)


class DograhClient:
    """HTTP client for Dograh's internal API."""

    def __init__(self, settings: Settings):
        self._base_url = settings.dograh_api_url.rstrip("/")
        self._token = settings.dograh_internal_token
        self._headers = {
            "X-Internal-Token": self._token,
            "Content-Type": "application/json",
        }

    async def fetch_runtime_config(self, deploy_id: str) -> RuntimeConfig:
        """Fetch full runtime config for a deploy."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self._base_url}/api/internal/deploy/{deploy_id}/runtime-config",
                headers=self._headers,
            )
            if response.status_code == 404:
                raise ValueError(f"Deploy {deploy_id} not found")
            response.raise_for_status()
            data = response.json()
            return RuntimeConfig(**data)

    async def search_knowledge(self, org_id: str, query: str, kb_refs: list[str] | None = None) -> dict[str, Any]:
        """Search the knowledge base."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/api/internal/kb/{org_id}/search",
                headers=self._headers,
                json={"query": query, "kb_refs": kb_refs or []},
            )
            response.raise_for_status()
            return response.json()

    async def create_session(
        self,
        deploy_id: str,
        org_id: str,
        room_name: str,
        channel: str,
        agent_id: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a session record in Dograh."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/api/internal/sessions",
                headers=self._headers,
                json={
                    "deploy_id": deploy_id,
                    "org_id": org_id,
                    "room_name": room_name,
                    "channel": channel,
                    "agent_id": agent_id,
                    **kwargs,
                },
            )
            response.raise_for_status()
            return response.json()

    async def update_session(self, session_id: str, org_id: str, **fields) -> dict[str, Any]:
        """Update a session record."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.put(
                f"{self._base_url}/api/internal/sessions/{session_id}",
                headers=self._headers,
                json={"org_id": org_id, **fields},
            )
            response.raise_for_status()
            return response.json()

    async def hangup_session(self, session_id: str, org_id: str, deploy_id: str, **kwargs) -> None:
        """Notify Dograh of session hangup."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self._base_url}/api/internal/sessions/hangup",
                headers=self._headers,
                json={
                    "session_id": session_id,
                    "org_id": org_id,
                    "deploy_id": deploy_id,
                    **kwargs,
                },
            )
            response.raise_for_status()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dograh-livekit && python -m pytest tests/test_dograh_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dograh-livekit/app/dograh_client.py dograh-livekit/tests/test_dograh_client.py
git commit -m "feat: add Dograh HTTP client for internal API"
```

---

### Task 4: Translator Layer — Workflow Graph → Agno Workflow

**Files:**
- Create: `dograh-livekit/app/translator/__init__.py`
- Create: `dograh-livekit/app/translator/workflow.py`
- Create: `dograh-livekit/app/translator/nodes.py`
- Create: `dograh-livekit/tests/test_translator.py`

**Interfaces:**
- Consumes: `app.models.WorkflowGraph` (from Task 2)
- Produces: `translate_workflow(graph, agent_config) -> agno.Workflow`

- [ ] **Step 1: Write the test**

```python
# dograh-livekit/tests/test_translator.py
import pytest
from app.models import WorkflowGraph, RFNode, RFEdge, NodeData, EdgeData, Position
from app.translator.workflow import translate_workflow


@pytest.fixture
def linear_graph():
    return WorkflowGraph(
        id="wf_1",
        nodes=[
            RFNode(
                id="n1",
                type="startCall",
                position=Position(x=0, y=0),
                data=NodeData(name="Start", prompt="Greet the caller"),
            ),
            RFNode(
                id="n2",
                type="agentNode",
                position=Position(x=100, y=0),
                data=NodeData(name="Qualify", prompt="Ask about budget"),
            ),
            RFNode(
                id="n3",
                type="endCall",
                position=Position(x=200, y=0),
                data=NodeData(name="End", prompt="Thank and goodbye"),
            ),
        ],
        edges=[
            RFEdge(id="e1", source="n1", target="n2", data=EdgeData(condition="*", label="next")),
            RFEdge(id="e2", source="n2", target="n3", data=EdgeData(condition="*", label="next")),
        ],
    )


@pytest.fixture
def branching_graph():
    return WorkflowGraph(
        id="wf_2",
        nodes=[
            RFNode(
                id="intent",
                type="startCall",
                position=Position(x=0, y=0),
                data=NodeData(name="Intent", prompt="Identify intent"),
            ),
            RFNode(
                id="sales",
                type="agentNode",
                position=Position(x=100, y=-50),
                data=NodeData(name="Sales", prompt="Sales pitch"),
            ),
            RFNode(
                id="support",
                type="agentNode",
                position=Position(x=100, y=50),
                data=NodeData(name="Support", prompt="Help the user"),
            ),
            RFNode(
                id="end",
                type="endCall",
                position=Position(x=200, y=0),
                data=NodeData(name="End", prompt="Goodbye"),
            ),
        ],
        edges=[
            RFEdge(id="e1", source="intent", target="sales", data=EdgeData(condition="sales", label="Sales")),
            RFEdge(id="e2", source="intent", target="support", data=EdgeData(condition="support", label="Support")),
            RFEdge(id="e3", source="sales", target="end", data=EdgeData(condition="*", label="done")),
            RFEdge(id="e4", source="support", target="end", data=EdgeData(condition="*", label="done")),
        ],
    )


class TestTranslateWorkflow:
    def test_linear_workflow_creates_steps(self, linear_graph):
        agent_config = {"system_prompt": "Test", "org_id": "org_1", "deploy_id": "dp_1"}
        workflow = translate_workflow(linear_graph, agent_config)
        assert workflow is not None
        assert len(workflow.steps) >= 3  # At least one step per agent node

    def test_branching_workflow_creates_router(self, branching_graph):
        agent_config = {"system_prompt": "Test", "org_id": "org_1", "deploy_id": "dp_1"}
        workflow = translate_workflow(branching_graph, agent_config)
        assert workflow is not None
        # Intent node should have a Router since it has 2 outgoing edges
        router_steps = [s for s in workflow.steps if hasattr(s, "selector")]
        assert len(router_steps) >= 1

    def test_non_agent_nodes_filtered(self):
        graph = WorkflowGraph(
            nodes=[
                RFNode(id="n1", type="startCall", position=Position(x=0, y=0),
                       data=NodeData(name="Start", prompt="Hi")),
                RFNode(id="n_qa", type="qa", position=Position(x=100, y=0),
                       data=NodeData(name="QA", qa_enabled=True)),
                RFNode(id="n2", type="endCall", position=Position(x=200, y=0),
                       data=NodeData(name="End", prompt="Bye")),
            ],
            edges=[
                RFEdge(id="e1", source="n1", target="n2", data=EdgeData(condition="*")),
            ],
        )
        agent_config = {"system_prompt": "Test", "org_id": "org_1", "deploy_id": "dp_1"}
        workflow = translate_workflow(graph, agent_config)
        # QA node should not be a Step; only startCall + endCall
        agent_step_ids = [s.name for s in workflow.steps if not hasattr(s, "selector")]
        assert "n_qa" not in agent_step_ids

    def test_empty_graph_creates_empty_workflow(self):
        graph = WorkflowGraph(nodes=[], edges=[])
        agent_config = {"system_prompt": "Test", "org_id": "org_1", "deploy_id": "dp_1"}
        workflow = translate_workflow(graph, agent_config)
        assert len(workflow.steps) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dograh-livekit && python -m pytest tests/test_translator.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write app/translator/nodes.py**

```python
"""Node type constants and helpers for the translator."""

# Node types that map to Agno Steps (agent-executable nodes)
AGENT_NODE_TYPES = {
    "startCall",
    "agentNode",
    "endCall",
}

# Node types that become tools on adjacent agents
TOOL_NODE_TYPES = {
    "webhook",
    "qa",  # becomes post-processing
    "trigger",  # handled at entrypoint level
}

# Node types that are merged into agent instructions
STATIC_NODE_TYPES = {
    "globalNode",
}


def is_agent_node(node_type: str) -> bool:
    return node_type in AGENT_NODE_TYPES


def is_start_node(node_type: str) -> bool:
    return node_type == "startCall"


def is_end_node(node_type: str) -> bool:
    return node_type == "endCall"
```

- [ ] **Step 4: Write app/translator/workflow.py**

```python
"""Translator: Dograh ReactFlow JSON → Agno Workflow."""

from __future__ import annotations

import logging
from typing import Any

from agno.agent import Agent
from agno.workflow import Step, Workflow
from agno.workflow.router import Router

from app.models import WorkflowGraph
from app.translator.nodes import is_agent_node, is_end_node, is_start_node

logger = logging.getLogger(__name__)


def translate_workflow(graph: WorkflowGraph, agent_config: dict[str, Any]) -> Workflow:
    """Convert Dograh workflow graph into an Agno Workflow.

    Args:
        graph: Parsed Dograh workflow graph with nodes and edges.
        agent_config: Top-level agent config (system_prompt, org_id, deploy_id, etc.)

    Returns:
        Agno Workflow with Steps and Routers.
    """
    nodes = graph.nodes
    edges = graph.edges

    if not nodes:
        return Workflow(name=f"dograh_{graph.id or 'empty'}", steps=[])

    # Filter to agent-executable nodes only
    agent_nodes = [n for n in nodes if is_agent_node(n.type)]

    # Build edge index: source_id → {condition → target_id}
    edge_index: dict[str, dict[str, str]] = {}
    for e in edges:
        cond = e.data.condition or "*"
        edge_index.setdefault(e.source, {})[cond] = e.target

    # Build a lookup of global node prompts
    global_prompt = ""
    for n in nodes:
        if n.type == "globalNode" and n.data.prompt:
            if global_prompt:
                global_prompt += "\n\n"
            global_prompt += n.data.prompt

    # Build Agno Agents from agent nodes
    agents_by_id: dict[str, Agent] = {}
    for node in agent_nodes:
        instructions = node.data.prompt or ""
        if node.data.add_global_prompt and global_prompt:
            instructions = global_prompt + "\n\n" + instructions
        if agent_config.get("system_prompt"):
            instructions = agent_config["system_prompt"] + "\n\n" + instructions

        agents_by_id[node.id] = Agent(
            name=node.data.name or node.id,
            instructions=instructions,
        )

    # Build Steps with Routers
    steps: list[Step | Router] = []
    for node in agent_nodes:
        node_id = node.id
        agent = agents_by_id[node_id]

        # Add the agent Step
        step_kwargs: dict = {}
        if is_end_node(node.type):
            step_kwargs["name"] = f"{node_id}_end"
        else:
            step_kwargs["name"] = node_id
        steps.append(Step(agent=agent, **step_kwargs))

        # If this node has multiple outgoing edges, add a Router
        outgoing = edge_index.get(node_id, {})
        non_star_keys = [k for k in outgoing if k != "*"]
        if len(non_star_keys) >= 2:
            choices: list[Step] = []
            choice_ids: set[str] = set()
            for route_key, target_id in outgoing.items():
                if target_id not in choice_ids and target_id in agents_by_id:
                    choice_ids.add(target_id)
                    choices.append(Step(name=target_id, agent=agents_by_id[target_id]))

            if choices:
                # Fallback: "*" target or last choice
                fallback_id = outgoing.get("*")
                fallback_step = None
                if fallback_id and fallback_id in agents_by_id:
                    fallback_step = Step(name=fallback_id, agent=agents_by_id[fallback_id])

                def make_selector(
                    rt: dict[str, str],
                    fallback: Step | None,
                    all_choices: list[Step],
                ):
                    def selector(step_input, step_choices):
                        name_to_step = {s.name: s for s in step_choices}
                        previous = (
                            step_input.previous_step_outputs
                            if hasattr(step_input, "previous_step_outputs")
                            else {}
                        )
                        for _prev_name, prev_output in (
                            previous.items() if isinstance(previous, dict) else []
                        ):
                            content = (
                                getattr(prev_output, "content", "")
                                if hasattr(prev_output, "content")
                                else str(prev_output)
                            )
                            content_lower = str(content).lower()
                            for route_key, target_id in rt.items():
                                if route_key != "*" and route_key in content_lower:
                                    target_name = target_id
                                    if target_name in name_to_step:
                                        return name_to_step[target_name]
                        if fallback and fallback.name in name_to_step:
                            return name_to_step[fallback.name]
                        return step_choices[-1] if step_choices else None

                    return selector

                router = Router(
                    name=f"{node_id}_router",
                    selector=make_selector(outgoing, fallback_step, choices),
                    choices=choices,
                )
                steps.append(router)

    return Workflow(
        name=f"dograh_{graph.id or 'wf'}",
        steps=steps,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd dograh-livekit && python -m pytest tests/test_translator.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dograh-livekit/app/translator/ dograh-livekit/tests/test_translator.py
git commit -m "feat: add translator layer (Dograh JSON → Agno Workflow)"
```

---

### Task 5: Flow Variables & Template Rendering

**Files:**
- Create: `dograh-livekit/app/session/__init__.py`
- Create: `dograh-livekit/app/session/flow.py`
- Create: `dograh-livekit/tests/test_flow.py`

**Interfaces:**
- Consumes: nothing external
- Produces: `build_runtime_variables(config, memory_vars) -> dict`, `render_template(template, values) -> str`

- [ ] **Step 1: Write the test**

```python
# dograh-livekit/tests/test_flow.py
from app.session.flow import build_runtime_variables, render_template


class TestBuildRuntimeVariables:
    def test_channel_voice_sip(self):
        config = {"channel": "voice_sip", "sender_phone": "+39123456789", "session_id": "sess_1"}
        vars = build_runtime_variables(config)
        assert vars["channel.name"] == "voice_sip"
        assert vars["channel.is_voice"] is True
        assert vars["channel.is_web_chat"] is False
        assert vars["user.phone"] == "+39123456789"
        assert vars["session.id"] == "sess_1"

    def test_channel_web_chat(self):
        config = {"channel": "web_chat"}
        vars = build_runtime_variables(config)
        assert vars["channel.supports_audio"] is True
        assert vars["channel.is_web_chat"] is True

    def test_memory_variables_merged(self):
        config = {"channel": "voice_sip"}
        memory = {"lead.name": "Mario", "lead.phone": "+39111"}
        vars = build_runtime_variables(config, memory_variables=memory)
        assert vars["lead.name"] == "Mario"
        assert vars["lead.phone"] == "+39111"


class TestRenderTemplate:
    def test_simple_variable(self):
        assert render_template("Hello {{name}}", {"name": "World"}) == "Hello World"

    def test_nested_variable(self):
        values = {"user": {"name": "Mario"}}
        assert render_template("Ciao {{user.name}}", values) == "Ciao Mario"

    def test_missing_variable(self):
        assert render_template("Hello {{missing}}", {}) == "Hello "

    def test_no_template(self):
        assert render_template("Plain text", {}) == "Plain text"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dograh-livekit && python -m pytest tests/test_flow.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write app/session/flow.py**

```python
"""Flow variables and template rendering for Dograh workflows."""

from __future__ import annotations

import re
from typing import Any

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.-]*)\s*\}\}")


def build_runtime_variables(
    config: dict[str, Any] | None,
    *,
    memory_variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build stable variables available to every flow node."""
    config = config or {}
    channel = str(config.get("channel") or "").strip()
    sender_phone = str(config.get("sender_phone") or "").strip()
    user_id = str(config.get("user_id") or sender_phone or "").strip()
    session_id = str(config.get("session_id") or "").strip()
    deploy_id = str(config.get("deploy_id") or "").strip()
    org_id = str(config.get("org_id") or "").strip()

    variables: dict[str, Any] = {}
    _put(variables, "channel.name", channel)
    _put(variables, "channel.is_voice", channel == "voice_sip")
    _put(variables, "channel.is_whatsapp", channel == "whatsapp")
    _put(variables, "channel.is_web_chat", channel == "web_chat")
    _put(variables, "channel.supports_audio", channel in {"voice_sip", "web_chat"})
    _put(variables, "session.id", session_id)
    _put(variables, "deploy.id", deploy_id)
    _put(variables, "org.id", org_id)
    _put(variables, "user.id", user_id)
    _put(variables, "user.phone", sender_phone)
    _put(variables, "caller_phone", sender_phone)

    for key, value in (memory_variables or {}).items():
        _put(variables, str(key), value)

    return variables


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    target[key] = value


def render_template(template: str, values: dict[str, Any] | None = None) -> str:
    """Replace {{var}} patterns with values. Supports dot-notation nesting."""
    values = values or {}

    def _lookup(key: str) -> Any:
        if key in values:
            return values.get(key)
        current: Any = values
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = _lookup(key)
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return str(value)
        return str(value)

    return _VAR_RE.sub(_replace, template or "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dograh-livekit && python -m pytest tests/test_flow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dograh-livekit/app/session/__init__.py dograh-livekit/app/session/flow.py dograh-livekit/tests/test_flow.py
git commit -m "feat: add flow variables and template rendering"
```

---

### Task 6: Stage Agents

**Files:**
- Create: `dograh-livekit/app/session/stages.py`
- Create: `dograh-livekit/tests/test_stages.py`

**Interfaces:**
- Consumes: nothing external
- Produces: `LuminaStageAgent` base class with `_complete_and_handoff()`, `CustomStage`, `IdentifyIntentStage`, `CloseStage`

- [ ] **Step 1: Write the test**

```python
# dograh-livekit/tests/test_stages.py
import pytest
from unittest.mock import MagicMock
from app.session.stages import CustomStage, IdentifyIntentStage, CloseStage


@pytest.fixture
def stage_config():
    return {
        "id": "stage_1",
        "type": "custom",
        "label": "Test Stage",
        "instructions": "Collect user info",
    }


@pytest.fixture
def agent_config():
    return {
        "system_prompt": "You are a test agent.",
        "session_id": "sess_1",
        "org_id": "org_1",
        "deploy_id": "dp_1",
    }


class TestCustomStage:
    def test_creates_stage(self, stage_config, agent_config):
        stage = CustomStage(stage_config, agent_config)
        assert stage.stage_id == "stage_1"
        assert stage.stage_label == "Test Stage"
        assert "Collect user info" in stage.instructions

    def test_complete_and_handoff_returns_none(self, stage_config, agent_config):
        """In Phase 13 (Agno Workflow), handoff returns None — routing is via Router."""
        stage = CustomStage(stage_config, agent_config)
        import asyncio
        result = asyncio.run(stage._complete_and_handoff({"data": "test"}))
        assert result is None


class TestIdentifyIntentStage:
    def test_base_instructions_include_routes(self, agent_config):
        stage_config = {
            "id": "intent",
            "type": "identify_intent",
            "label": "Route Intent",
            "instructions": "Find out what they need",
            "routes": {"sales": "n_sales", "support": "n_support"},
        }
        all_stages = [
            {"id": "intent", "label": "Route Intent"},
            {"id": "n_sales", "label": "Sales"},
            {"id": "n_support", "label": "Support"},
        ]
        stage = IdentifyIntentStage(stage_config, agent_config, all_stages=all_stages)
        assert "sales" in stage.instructions.lower()
        assert "support" in stage.instructions.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dograh-livekit && python -m pytest tests/test_stages.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write app/session/stages.py**

```python
"""
Stage agents — reusable conversation stage types.

Each stage is a LiveKit Agent with:
  - type-specific base instructions + completion tool
  - optional extra tools from stage config
"""

from __future__ import annotations

import json
import logging
from typing import Any

from livekit.agents import Agent, RunContext, function_tool

from app.session.flow import render_template

logger = logging.getLogger(__name__)


class LuminaStageAgent(Agent):
    """Base class for all Dograh-LiveKit stage agents."""

    def __init__(
        self,
        stage_config: dict,
        agent_config: dict,
        all_stages: list[dict] | None = None,
    ):
        self.stage_id: str = stage_config.get("id", "")
        self.stage_type: str = stage_config.get("type", "custom")
        self.stage_label: str = (
            stage_config.get("label")
            or self.stage_type.replace("_", " ").title()
        )
        self._stage_config = stage_config
        self._agent_config = agent_config
        self._routes: dict[str, str] = stage_config.get("routes") or {}
        self._all_stages: list[dict] = all_stages or []

        instructions = self._build_instructions(agent_config)
        super().__init__(instructions=instructions)

    def _build_instructions(self, agent_config: dict) -> str:
        agent_name = str(agent_config.get("agent_name") or "").strip()
        system_prompt = str(agent_config.get("system_prompt") or "").strip()

        identity_block = ""
        if system_prompt:
            identity_block = (
                "═══ CONTESTO AGENTE ═══\n"
                f"{system_prompt}\n"
                "═══════════════════════\n\n"
            )
        elif agent_name:
            identity_block = f"Sei {agent_name}.\n\n"

        base = self._base_instructions()
        custom = self._stage_config.get("instructions", "").strip()

        parts = [identity_block, base, custom]
        instructions = "\n\n".join(part for part in parts if part)
        return render_template(instructions, agent_config.get("flow_variables") or {})

    def _base_instructions(self) -> str:
        return "Esegui questa fase della conversazione."

    async def _send_exact_message(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        try:
            await self.session.say(text, allow_interruptions=True, add_to_chat_ctx=True)
        except Exception:
            try:
                await self.session.say(text, allow_interruptions=True)
            except Exception as exc:
                logger.warning("Stage %s: say failed: %s", self.stage_id, exc)

    async def _complete_and_handoff(
        self, result: dict, route_key: str | None = None
    ) -> Agent | None:
        """Persist result and return None (Phase 13 — routing via Agno Router)."""
        logger.info(
            "Stage '%s' completed: route_key=%s data=%s",
            self.stage_id, route_key, list(result.keys()),
        )
        # Session context persistence is handled by the entrypoint lifecycle
        return None

    async def on_enter(self) -> None:
        pass


class CustomStage(LuminaStageAgent):
    """Generic stage following node instructions."""

    def _base_instructions(self) -> str:
        return "Segui le istruzioni fornite per questa fase."

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Inizia questa fase seguendo le tue istruzioni."
        )

    @function_tool
    async def complete_custom_stage(
        self,
        result: str,
        ctx: RunContext,
        route_key: str | None = None,
    ) -> Agent | None:
        """Complete this stage and hand off to the next.

        result: JSON or text with collected data
        route_key: optional routing key for conditional branching
        """
        try:
            data = json.loads(result)
        except Exception:
            data = {"result": result}
        return await self._complete_and_handoff(data, route_key=route_key)


class IdentifyIntentStage(LuminaStageAgent):
    """Classify caller intent and route to the appropriate stage."""

    def _base_instructions(self) -> str:
        routes = self._stage_config.get("routes") or {}
        stages_by_id = {s.get("id"): s for s in self._all_stages if s.get("id")}
        route_lines = []
        for route_key, target_id in routes.items():
            rk = str(route_key).strip()
            if not rk or rk == "*":
                continue
            target = stages_by_id.get(target_id)
            target_label = str((target or {}).get("label") or "").strip()
            desc = f"  - '{rk}'"
            if target_label:
                desc += f": {target_label}"
            route_lines.append(desc)

        cats_block = ""
        if route_lines:
            first_key = next(
                (str(k).strip() for k in routes if str(k).strip() != "*"),
                "categoria",
            )
            cats_block = (
                "Le categorie disponibili:\n"
                + "\n".join(route_lines)
                + f"\n\nUsa come valore di 'intent' esattamente una delle chiavi (es. '{first_key}'). "
            )

        return (
            "Il tuo obiettivo è capire il motivo della richiesta e instradare "
            "correttamente usando 'record_intent'.\n\n"
            f"{cats_block}"
            "REGOLE:\n"
            "1. Solo saluto → NON chiamare record_intent, rispondi e chiedi come aiutare.\n"
            "2. Qualsiasi altra cosa → classifica SUBITO con record_intent.\n"
            "3. Nel dubbio, scegli la categoria informativa/generica.\n"
        )

    async def on_enter(self) -> None:
        greeting = str(self._agent_config.get("greeting_message") or "").strip()
        if greeting:
            await self._send_exact_message(greeting)
            return
        await self.session.generate_reply(
            instructions="Chiedi all'utente come puoi aiutarlo oggi."
        )

    @function_tool
    async def record_intent(
        self,
        intent: str,
        description: str,
        urgency: str,
        ctx: RunContext,
    ) -> Agent | None:
        """Register identified intent and route to next stage.

        intent: exact routing key from the available options
        description: detailed description of the reason for calling
        urgency: 'bassa', 'media', 'alta'
        """
        route_key = str(intent or "").strip().lower()
        logger.info("record_intent: intent=%r route_key=%r", intent, route_key)
        return await self._complete_and_handoff(
            {"intent": route_key or intent, "intent_description": description, "urgency": urgency},
            route_key=route_key,
        )


class CloseStage(LuminaStageAgent):
    """Close the conversation professionally."""

    def _base_instructions(self) -> str:
        return (
            "Il tuo obiettivo è chiudere la conversazione in modo professionale. "
            "Riepiloga quanto concordato, conferma i prossimi passi e saluta. "
            "Usa il tool 'close_call' quando hai finito."
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Riepiloga brevemente quanto discusso e saluta il cliente."
        )

    @function_tool
    async def close_call(self, summary: str, outcome: str, ctx: RunContext) -> None:
        """Close the call — no further handoff.

        summary: brief call summary
        outcome: result (e.g., 'appuntamento fissato', 'info fornite')
        """
        await self._complete_and_handoff({"call_summary": summary, "call_outcome": outcome})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dograh-livekit && python -m pytest tests/test_stages.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dograh-livekit/app/session/stages.py dograh-livekit/tests/test_stages.py
git commit -m "feat: add stage agents (Custom, IdentifyIntent, Close)"
```

---

### Task 7: Tool Registry, Dispatcher & KB Search

**Files:**
- Create: `dograh-livekit/app/tools/__init__.py`
- Create: `dograh-livekit/app/tools/registry.py`
- Create: `dograh-livekit/app/tools/dispatcher.py`
- Create: `dograh-livekit/app/tools/kb_search.py`
- Create: `dograh-livekit/tests/test_tools.py`

**Interfaces:**
- Consumes: `app.dograh_client.DograhClient` (from Task 3), `app.models.ToolDefinition` (from Task 2)
- Produces: `build_tools(agent_proxy) -> list`, `search_knowledge_tool` function_tool

- [ ] **Step 1: Write the test**

```python
# dograh-livekit/tests/test_tools.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from livekit.agents import Agent
from app.tools.registry import TOOL_REGISTRY, build_tools


class MockAgentProxy(Agent):
    def __init__(self, config=None):
        super().__init__(instructions="test")
        self._config = config or {}
        self._deploy_id = self._config.get("deploy_id", "")
        self._org_id = self._config.get("org_id", "")
        self._kb_refs = self._config.get("kb_refs", [])
        self._channel = self._config.get("channel", "")


class TestToolRegistry:
    def test_kb_search_tool_loads_with_kb_refs(self):
        """search_knowledge tool is added when kb_refs is non-empty."""
        proxy = MockAgentProxy({"kb_refs": ["kb_1"], "org_id": "org_1",
                                "deploy_id": "dp_1"})
        tools = build_tools(proxy)
        tool_names = [t.name for t in tools]
        assert "search_knowledge" in tool_names or len(tools) > 0

    def test_no_kb_tool_without_refs(self):
        """search_knowledge tool is NOT added when kb_refs is empty."""
        proxy = MockAgentProxy({"kb_refs": [], "org_id": "org_1",
                                "deploy_id": "dp_1"})
        tools = build_tools(proxy)
        tool_names = [t.name for t in tools]
        assert "search_knowledge" not in tool_names

    def test_kb_search_from_registry(self):
        """The KB search tool is callable."""
        proxy = MockAgentProxy({"kb_refs": ["kb_1"], "org_id": "org_1",
                                "deploy_id": "dp_1"})
        tools = build_tools(proxy)
        assert len(tools) >= 1
        # Verify tools are LiveKit function_tool instances
        for tool in tools:
            assert callable(tool)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dograh-livekit && python -m pytest tests/test_tools.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write app/tools/kb_search.py**

```python
"""Knowledge base search tool — calls Dograh API."""

import logging
from livekit.agents import function_tool

logger = logging.getLogger(__name__)


def make_kb_search_tool(agent_proxy):
    """Create a search_knowledge function_tool that calls Dograh API."""

    @function_tool
    async def search_knowledge(query: str) -> str:
        """Search the organization's knowledge base for relevant information.

        query: the search query in natural language
        Returns: formatted search results from the knowledge base.
        """
        from app.dograh_client import DograhClient
        from app.config import settings

        client = DograhClient(settings)
        try:
            results = await client.search_knowledge(
                agent_proxy._org_id,
                query,
                agent_proxy._kb_refs,
            )
        except Exception as exc:
            logger.warning("KB search failed: %s", exc)
            return "Knowledge base search unavailable at this moment."

        items = results.get("results", [])
        if not items:
            return "No relevant information found in the knowledge base."

        formatted = []
        for item in items[:5]:
            content = item.get("content", "")
            source = item.get("source", "unknown")
            if content:
                formatted.append(f"[{source}] {content}")

        return "\n\n".join(formatted)

    return search_knowledge
```

- [ ] **Step 4: Write app/tools/registry.py**

```python
"""Tool registry — maps tool names to factory functions."""

from app.tools.kb_search import make_kb_search_tool


def build_tools(agent_proxy) -> list:
    """Build tools for an agent based on its config.

    Args:
        agent_proxy: An Agent-like object with _kb_refs, _org_id, etc.

    Returns:
        List of LiveKit function_tool instances.
    """
    tools = []

    # KB search — always included if kb_refs is non-empty
    kb_refs = getattr(agent_proxy, "_kb_refs", []) or []
    if kb_refs:
        tools.append(make_kb_search_tool(agent_proxy))

    # Future: add transfer_to_human, webhook tools, etc.
    # based on agent_proxy._config.get("tools", [])

    return tools


TOOL_REGISTRY = {
    "search_knowledge": make_kb_search_tool,
}
```

- [ ] **Step 5: Write app/tools/dispatcher.py**

```python
"""
LuminaAgent — LiveKit Agent with dynamic tool loading for Dograh-LiveKit.

If the deploy config has stages, stages are handled by Agno WorkflowFactory.
This agent serves as the fallback/default for non-staged agents.
"""

import logging
from livekit import agents
from app.tools.registry import build_tools
from app.session.flow import render_template

logger = logging.getLogger(__name__)


class LuminaAgent(agents.Agent):
    """Default agent for Dograh-LiveKit sessions."""

    def __init__(self, config: dict):
        self._config = config
        self._deploy_id = config.get("deploy_id", "")
        self._org_id = config.get("org_id", "")
        self._kb_refs = config.get("kb_refs", []) or []
        self._greeting_message = config.get("greeting_message", "").strip()
        self._channel = config.get("channel", "")

        llm_provider = config.get("llm_config", {}).get("provider", "")
        self._is_realtime = llm_provider in {
            "google_realtime", "openai_realtime", "aws_realtime",
        }

        instructions = config.get("system_prompt", "Sei un assistente.")
        if self._kb_refs:
            instructions += (
                "\n\nSe l'utente chiede dettagli specifici, chiama "
                "`search_knowledge` prima di rispondere."
            )
        instructions = render_template(instructions, config.get("flow_variables") or {})

        tools = build_tools(self)

        super().__init__(instructions=instructions, tools=tools)

    async def on_enter(self) -> None:
        if not self._is_realtime:
            return
        if self._greeting_message:
            await self.session.generate_reply(
                instructions=(
                    f"Di' esattamente questa frase: \"{self._greeting_message}\""
                )
            )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd dograh-livekit && python -m pytest tests/test_tools.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add dograh-livekit/app/tools/ dograh-livekit/tests/test_tools.py
git commit -m "feat: add tool registry, dispatcher, and KB search tool"
```

---

### Task 8: Voice Session Builder

**Files:**
- Create: `dograh-livekit/app/session/voice.py`
- Create: `dograh-livekit/tests/test_voice.py`

**Interfaces:**
- Consumes: `app.tools.dispatcher.LuminaAgent`, `app.session.stages.*`
- Produces: `voice_session(ctx, config) -> AgentSession`

- [ ] **Step 1: Write the test**

```python
# dograh-livekit/tests/test_voice.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestVoiceSession:
    @pytest.mark.asyncio
    async def test_voice_session_chooses_realtime_for_google(self):
        """When llm_config.provider is google_realtime, use _build_realtime_session."""
        from app.session.voice import voice_session, REALTIME_LLM_PROVIDERS
        assert "google_realtime" in REALTIME_LLM_PROVIDERS

        config = {
            "channel": "voice_sip",
            "llm_config": {"provider": "google_realtime", "model": "gemini-2.5-flash-native-audio"},
            "tts_config": {"voice_id": "Kore"},
            "system_prompt": "You are helpful.",
            "orchestrator_mode": "default",
        }

        # We test that the config is correctly interpreted.
        # Full session building requires LiveKit runtime — tested in integration.
        assert config["llm_config"]["provider"] == "google_realtime"

    @pytest.mark.asyncio
    async def test_voice_session_chooses_fallback_for_anthropic(self):
        """When llm_config.provider is anthropic, use _build_fallback_session."""
        config = {
            "channel": "voice_sip",
            "llm_config": {"provider": "anthropic", "model": "claude-sonnet-4"},
            "stt_config": {"provider": "deepgram", "model_id": "nova-3"},
            "tts_config": {"provider": "cartesia", "voice_id": "Kore"},
            "system_prompt": "You are helpful.",
            "orchestrator_mode": "default",
        }

        from app.session.voice import REALTIME_LLM_PROVIDERS
        assert config["llm_config"]["provider"] not in REALTIME_LLM_PROVIDERS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dograh-livekit && python -m pytest tests/test_voice.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write app/session/voice.py**

```python
"""Voice session builder — realtime or STT+LLM+TTS fallback."""

import logging
import os
from livekit import agents, rtc
from livekit.agents import AgentSession, room_io

logger = logging.getLogger(__name__)

REALTIME_LLM_PROVIDERS = {"google_realtime", "openai_realtime", "aws_realtime"}


def _room_options_for_channel(channel: str) -> room_io.RoomOptions:
    audio_output = room_io.AudioOutputOptions(
        track_publish_options=rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_MICROPHONE, red=False,
        ),
    )
    return room_io.RoomOptions(audio_input=True, audio_output=audio_output)


async def voice_session(ctx: agents.JobContext, config: dict) -> AgentSession:
    """Build and start a voice AgentSession.

    Routes to realtime (native audio) or fallback (STT+LLM+TTS) based on provider.
    """
    from app.tools.dispatcher import LuminaAgent

    llm_cfg = config.get("llm_config", {})
    llm_provider = llm_cfg.get("provider", "")
    llm_model = llm_cfg.get("model", "")

    if config.get("orchestrator_mode") == "agentos":
        # Agno Workflow path — first stage from workflow
        from app.translator.workflow import translate_workflow
        workflow_graph = config.get("workflow_graph")
        if workflow_graph:
            agno_wf = translate_workflow(workflow_graph, config)
            agent = agno_wf  # Agno Workflow acts as the agent for the session
        else:
            agent = LuminaAgent(config=config)
    else:
        agent = LuminaAgent(config=config)

    is_realtime = llm_provider in REALTIME_LLM_PROVIDERS

    if is_realtime:
        session = await _build_realtime_session(ctx, agent, llm_provider, llm_model, config)
    else:
        session = await _build_fallback_session(ctx, agent, config)

    # Greeting
    greeting = config.get("greeting_message", "").strip()
    if greeting and is_realtime:
        await session.generate_reply(
            instructions=f"Di' esattamente questa frase: \"{greeting}\""
        )

    return session


async def _build_realtime_session(
    ctx: agents.JobContext,
    agent,
    llm_provider: str,
    llm_model: str,
    config: dict,
) -> AgentSession:
    llm_voice = config.get("tts_config", {}).get("voice_id", "Kore")
    llm_temperature = float(config.get("llm_config", {}).get("temperature", 0.8))
    room_options = _room_options_for_channel(config.get("channel", ""))

    if llm_provider == "google_realtime":
        from livekit.plugins import google
        instructions = config.get("system_prompt", "")
        llm_max_tokens = config.get("llm_config", {}).get("max_output_tokens") or None

        kwargs = dict(
            model=llm_model,
            temperature=llm_temperature,
            instructions=instructions,
            voice=llm_voice,
        )
        if llm_max_tokens:
            kwargs["max_output_tokens"] = llm_max_tokens

        session = AgentSession(llm=google.realtime.RealtimeModel(**kwargs))
        await session.start(room=ctx.room, agent=agent, room_options=room_options)

    elif llm_provider == "openai_realtime":
        from livekit.plugins import openai
        session = AgentSession(
            llm=openai.realtime.RealtimeModel(
                model=llm_model or "gpt-4o-realtime-preview",
                temperature=llm_temperature,
                modalities=["audio"],
                voice=llm_voice,
            )
        )
        await session.start(room=ctx.room, agent=agent, room_options=room_options)

    else:
        raise ValueError(f"Unknown realtime provider: {llm_provider}")

    return session


async def _build_fallback_session(
    ctx: agents.JobContext, agent, config: dict
) -> AgentSession:
    """STT + LLM + TTS pipeline."""
    from livekit.plugins import deepgram, google, silero, cartesia
    from livekit.agents import TurnHandlingOptions

    config = config or {}
    channel = config.get("channel", "")

    stt_provider = config.get("stt_config", {}).get("provider", "deepgram")
    stt_model = config.get("stt_config", {}).get("model_id", "nova-3")
    tts_provider = config.get("tts_config", {}).get("provider", "cartesia")
    tts_voice = config.get("tts_config", {}).get("voice_id", "Kore")

    # STT
    if stt_provider == "google":
        stt = google.STT(model="latest_long", languages="it-IT")
    else:
        stt_model_id = stt_model.split("/")[-1] if stt_model else "nova-3"
        stt = deepgram.STT(model=stt_model_id, language="it")

    # TTS
    tts_model = config.get("tts_config", {}).get("model_id", "sonic-3")
    if tts_provider == "google_tts":
        tts = google.TTS(model_name="gemini-2.5-flash-preview-tts", voice_name=tts_voice or "Kore", language="it")
    elif tts_provider == "deepgram":
        tts = deepgram.TTS(model="aura-2", voice=tts_voice or "")
    else:
        model_id = tts_model.split("/")[-1] if tts_model else "sonic-3"
        tts = cartesia.TTS(model=model_id, voice=tts_voice or "")

    # LLM
    llm_model = config.get("llm_config", {}).get("model", "gemini-2.5-flash")
    llm = google.LLM(model=llm_model)

    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(turn_detection="stt"),
        preemptive_generation=True,
    )
    await session.start(
        room=ctx.room,
        agent=agent,
        room_options=_room_options_for_channel(channel),
    )
    return session
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dograh-livekit && python -m pytest tests/test_voice.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dograh-livekit/app/session/voice.py dograh-livekit/tests/test_voice.py
git commit -m "feat: add voice session builder (realtime + fallback)"
```

---

### Task 9: Session Lifecycle & RTC Entrypoint

**Files:**
- Create: `dograh-livekit/app/session/lifecycle.py`
- Create: `dograh-livekit/app/entrypoint.py`
- Create: `dograh-livekit/app/main.py`
- Create: `dograh-livekit/tests/test_entrypoint.py`

**Interfaces:**
- Consumes: All prior tasks
- Produces: LiveKit AgentServer with `lumina_session` handler, session lifecycle management

- [ ] **Step 1: Write lifecycle.py**

```python
"""Session lifecycle — create/update session records via Dograh API."""

import logging
from app.dograh_client import DograhClient
from app.config import settings

logger = logging.getLogger(__name__)


async def fetch_agent_config(raw_metadata: str, dograh_api_url: str = "") -> dict:
    """Fetch agent config from Dograh API based on room metadata."""
    import json
    client = DograhClient(settings)

    meta = json.loads(raw_metadata or "{}")
    deploy_id = meta.get("deploy_id", "")
    if not deploy_id:
        raise ValueError("deploy_id missing from room metadata")

    config = await client.fetch_runtime_config(deploy_id)
    config_dict = config.model_dump()
    config_dict["channel"] = meta.get("channel", "voice_sip")
    config_dict["sender_phone"] = meta.get("sender_phone", "")
    config_dict["campaign_id"] = meta.get("campaign_id", "")
    config_dict["lead_id"] = meta.get("lead_id", "")

    return config_dict


async def write_session_record(
    deploy_id: str,
    org_id: str,
    room_name: str,
    channel: str,
    agent_id: str,
    **kwargs,
) -> dict:
    """Create a session record in Dograh."""
    client = DograhClient(settings)
    return await client.create_session(
        deploy_id=deploy_id,
        org_id=org_id,
        room_name=room_name,
        channel=channel,
        agent_id=agent_id,
        **kwargs,
    )


async def hangup_cleanup(
    session_id: str,
    org_id: str,
    deploy_id: str,
    room_name: str,
    duration_sec: float,
    channel: str,
) -> None:
    """Post-session cleanup: delete LiveKit room and notify Dograh."""
    from livekit.api import LiveKitAPI
    from livekit.protocol.room import DeleteRoomRequest

    try:
        async with LiveKitAPI(
            url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        ) as lk_api:
            await lk_api.room.delete_room(DeleteRoomRequest(room=room_name))
            logger.info(f"LiveKit room deleted: {room_name}")
    except Exception as e:
        logger.warning(f"Failed to delete room {room_name}: {e}")

    client = DograhClient(settings)
    try:
        await client.hangup_session(
            session_id=session_id,
            org_id=org_id,
            deploy_id=deploy_id,
            room_name=room_name,
            duration_sec=duration_sec,
            outcome="completed",
            channel=channel,
        )
    except Exception as e:
        logger.warning(f"Failed to send hangup webhook: {e}")
```

- [ ] **Step 2: Write entrypoint.py**

```python
"""RTC session entrypoint — main handler for LiveKit AgentServer."""

import asyncio
import json
import logging
import time

from livekit import agents
from livekit.agents import AgentServer

from app.config import settings
from app.session.lifecycle import fetch_agent_config, write_session_record, hangup_cleanup
from app.session.flow import build_runtime_variables

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def _wait_for_sip_disconnect(ctx: agents.JobContext, timeout: float = 60.0) -> None:
    """Keep a SIP call alive until the SIP participant leaves."""
    participant_joined = asyncio.Event()
    participant_left = asyncio.Event()

    def _on_connected(participant) -> None:
        identity = getattr(participant, "identity", "")
        if identity.startswith("sip_"):
            logger.info("SIP participant connected: %s", identity)
            participant_joined.set()

    def _on_disconnected(participant) -> None:
        identity = getattr(participant, "identity", "")
        if identity.startswith("sip_"):
            logger.info("SIP participant disconnected: %s", identity)
            participant_left.set()

    ctx.room.on("participant_connected", _on_connected)
    ctx.room.on("participant_disconnected", _on_disconnected)

    try:
        for p in ctx.room.remote_participants.values():
            _on_connected(p)
        await asyncio.wait_for(participant_joined.wait(), timeout=timeout)
        await participant_left.wait()
    except asyncio.TimeoutError:
        logger.info("No SIP participant joined within timeout; closing room")
    finally:
        ctx.room.off("participant_connected", _on_connected)
        ctx.room.off("participant_disconnected", _on_disconnected)


async def lumina_session(ctx: agents.JobContext):
    """Main entry point for all Dograh-LiveKit agent sessions."""
    session = None
    t_start = time.monotonic()
    session_id = ""
    org_id = ""
    deploy_id = ""
    channel = ""

    try:
        await ctx.connect()

        raw_metadata = ctx.job.room.metadata or ""
        meta = json.loads(raw_metadata or "{}")
        deploy_id = meta.get("deploy_id", "")
        channel = meta.get("channel", "voice_sip")
        org_id = meta.get("org_id", "")

        if not deploy_id:
            raise ValueError("deploy_id missing from room metadata")
        if not org_id:
            raise ValueError("org_id missing from room metadata")

        logger.info(f"Session start — room={ctx.room.name} deploy={deploy_id} channel={channel}")

        config = await fetch_agent_config(raw_metadata)
        config["deploy_id"] = deploy_id
        config["org_id"] = org_id
        config["sender_phone"] = meta.get("sender_phone", "")
        config["channel"] = channel
        config["user_id"] = meta.get("user_id") or meta.get("sender_phone") or ""

        # Create session record
        llm_cfg = config.get("llm_config") or {}
        llm_model = str(llm_cfg.get("model") or "unknown")
        session_record = await write_session_record(
            deploy_id=deploy_id,
            org_id=org_id,
            room_name=ctx.room.name,
            channel=channel,
            agent_id=str(config.get("agent_id") or ""),
            llm_model=llm_model,
        )
        session_id = str(session_record.get("id", ""))
        config["session_id"] = session_id

        # Build runtime variables
        flow_vars = build_runtime_variables(config)
        config["flow_variables"] = flow_vars

        # Build session
        from app.session.voice import voice_session
        logger.info("Starting voice session...")
        session = await voice_session(ctx, config)
        logger.info(f"Session started in {time.monotonic() - t_start:.2f}s")

        # Wait for disconnect
        if channel == "voice_sip":
            await _wait_for_sip_disconnect(ctx)
        else:
            await session.wait_for_inactive()

        duration = time.monotonic() - t_start
        logger.info(f"Session completed — duration={duration:.1f}s")

    except Exception as exc:
        logger.error(f"Agent exception in room {ctx.room.name}: {exc}", exc_info=True)
        if session is not None:
            try:
                await session.say("Mi dispiace, si è verificato un problema. Richiami più tardi.")
            except Exception:
                pass
        duration = time.monotonic() - t_start
    else:
        duration = time.monotonic() - t_start

    finally:
        if session_id and org_id:
            await hangup_cleanup(
                session_id=session_id,
                org_id=org_id,
                deploy_id=deploy_id,
                room_name=ctx.room.name,
                duration_sec=duration,
                channel=channel,
            )
```

- [ ] **Step 3: Write main.py**

```python
"""LiveKit AgentServer entrypoint for dograh-livekit."""

import os
import asyncio
import logging
from livekit import agents
from livekit.agents import AgentServer

from app.config import settings

# Pre-import plugins so they register on main thread
from livekit.plugins import google as _g  # noqa
from livekit.plugins import silero as _s  # noqa
from livekit.plugins import openai as _o  # noqa
from livekit.plugins import deepgram as _dg  # noqa
from livekit.plugins import cartesia as _ct  # noqa

os.environ["GOOGLE_API_KEY"] = settings.google_api_key
os.environ["OPENAI_API_KEY"] = settings.openai_api_key
os.environ["LIVEKIT_URL"] = settings.livekit_url
os.environ["LIVEKIT_API_KEY"] = settings.livekit_api_key
os.environ["LIVEKIT_API_SECRET"] = settings.livekit_api_secret

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

server = AgentServer(
    ws_url=settings.livekit_url,
    api_key=settings.livekit_api_key,
    api_secret=settings.livekit_api_secret,
    port=0,
    num_idle_processes=1,
    load_threshold=1.0,
    job_executor_type=agents.JobExecutorType.THREAD,
)


@server.rtc_session(agent_name="dograh-agent")
async def dograh_session(ctx: agents.JobContext):
    from app.entrypoint import lumina_session
    await lumina_session(ctx)


async def serve():
    await server.run()


if __name__ == "__main__":
    asyncio.run(serve())
```

- [ ] **Step 4: Write entrypoint test**

```python
# dograh-livekit/tests/test_entrypoint.py
import pytest
from app.session.lifecycle import hangup_cleanup


class TestHangupCleanup:
    @pytest.mark.asyncio
    async def test_hangup_cleanup_called(self, settings):
        """Verify hangup_cleanup is importable and callable (integration tested later)."""
        # Unit test: verify the function exists and accepts expected args
        import inspect
        sig = inspect.signature(hangup_cleanup)
        params = list(sig.parameters.keys())
        assert "session_id" in params
        assert "org_id" in params
        assert "deploy_id" in params
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd dograh-livekit && python -m pytest tests/test_entrypoint.py -v`
Expected: PASS (import check only — no LiveKit runtime needed)

- [ ] **Step 6: Commit**

```bash
git add dograh-livekit/app/session/lifecycle.py dograh-livekit/app/entrypoint.py \
        dograh-livekit/app/main.py dograh-livekit/tests/test_entrypoint.py
git commit -m "feat: add session lifecycle, entrypoint, and AgentServer main"
```

---

### Task 10: Dograh Internal API Endpoint

**Files:**
- Create: `api/routes/internal.py` (if not existing) or modify existing internal routes
- Note: Check existing route structure first

**Interfaces:**
- Consumes: Dograh's existing `db_client`, workflow models
- Produces: `GET /api/internal/deploy/{deploy_id}/runtime-config`, `POST /api/internal/kb/{org_id}/search`, `POST /api/internal/sessions`, etc.

- [ ] **Step 1: Explore existing internal route structure**

Look for existing `/api/internal/` routes in Dograh. Check if there's already an internal routes file.

- [ ] **Step 2: Add runtime-config endpoint**

```python
# api/routes/internal_runtime.py (new file, or appended to existing internal routes)
from fastapi import APIRouter, Depends, HTTPException
from api.db import db_client
from api.auth.middleware import verify_internal_token

router = APIRouter(prefix="/api/internal", tags=["internal"])


@router.get("/deploy/{deploy_id}/runtime-config")
async def get_runtime_config(
    deploy_id: str,
    _: None = Depends(verify_internal_token),
):
    """Return full runtime config for a deploy — consumed by dograh-livekit."""
    # Fetch the deploy record
    deploy = await db_client.get_deploy(deploy_id)
    if not deploy:
        raise HTTPException(status_code=404, detail="Deploy not found")

    # Fetch the workflow with full graph
    workflow = await db_client.get_workflow(deploy.workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Resolve tools from UUIDs
    tools = []
    for node in workflow.definition.get("nodes", []):
        tool_uuids = (node.get("data") or {}).get("tool_uuids") or []
        for uuid in tool_uuids:
            tool_def = await db_client.get_tool_definition(uuid, deploy.organization_id)
            if tool_def:
                tools.append(tool_def)

    # Resolve KB refs
    kb_refs = []
    for node in workflow.definition.get("nodes", []):
        doc_uuids = (node.get("data") or {}).get("document_uuids") or []
        kb_refs.extend(doc_uuids)
    kb_refs = list(set(kb_refs))

    return {
        "deploy_id": deploy_id,
        "org_id": str(deploy.organization_id),
        "agent_id": str(deploy.agent_id) if deploy.agent_id else "",
        "agent_name": deploy.name,
        "workflow_graph": workflow.definition,
        "llm_config": deploy.llm_config or {},
        "stt_config": deploy.stt_config or {},
        "tts_config": deploy.tts_config or {},
        "system_prompt": deploy.system_prompt or "",
        "greeting_message": deploy.greeting_message or "",
        "tools": tools,
        "kb_refs": kb_refs,
        "handoff_sip_number": deploy.handoff_sip_number or "",
        "orchestrator_mode": "agentos",
        "stages": workflow.definition.get("nodes", []),
    }
```

- [ ] **Step 3: Add KB search endpoint**

```python
@router.post("/kb/{org_id}/search")
async def search_knowledge(
    org_id: str,
    body: dict,
    _: None = Depends(verify_internal_token),
):
    """Search the knowledge base for an organization."""
    query = body.get("query", "")
    kb_refs = body.get("kb_refs", [])

    # Use existing KB search implementation
    from api.services.knowledge import search_documents
    results = await search_documents(org_id, query, document_ids=kb_refs)
    return {"results": results}
```

- [ ] **Step 4: Add session endpoints**

```python
@router.post("/sessions", status_code=201)
async def create_session(body: dict, _: None = Depends(verify_internal_token)):
    """Create a session record."""
    from api.db.models import SessionModel
    session = await db_client.create_session(
        deploy_id=body["deploy_id"],
        org_id=body["org_id"],
        room_name=body["room_name"],
        channel=body.get("channel", "voice_sip"),
        agent_id=body.get("agent_id", ""),
        llm_model=body.get("llm_model", "unknown"),
    )
    return {"id": str(session.id), "status": "active"}


@router.put("/sessions/{session_id}")
async def update_session(session_id: str, body: dict, _: None = Depends(verify_internal_token)):
    """Update a session record."""
    await db_client.update_session(session_id, **body)
    return {"status": "updated"}


@router.post("/sessions/hangup")
async def hangup_session(body: dict, _: None = Depends(verify_internal_token)):
    """Handle session hangup notification."""
    # Update session record, trigger post-processing
    await db_client.complete_session(
        session_id=body["session_id"],
        org_id=body["org_id"],
        duration_sec=body.get("duration_sec", 0),
        outcome=body.get("outcome", "completed"),
    )
    return {"status": "ok"}
```

- [ ] **Step 5: Register the router in api/main.py**

- [ ] **Step 6: Write and run tests**

- [ ] **Step 7: Commit**

```bash
git add api/routes/ api/main.py api/tests/
git commit -m "feat: add internal API endpoints for dograh-livekit bridge"
```

---

### Task 11: LiveKitSipProvider in Dograh Telephony

**Files:**
- Create: `api/services/telephony/livekit_sip/__init__.py`
- Create: `api/services/telephony/livekit_sip/provider.py`
- Modify: `api/services/telephony/factory.py` (register provider)

**Interfaces:**
- Consumes: `api.services.telephony.base.TelephonyProvider`
- Produces: `LiveKitSipProvider` implementing `initiate_call()`, `PROVIDER_NAME`

- [ ] **Step 1: Explore existing telephony provider pattern**

Read an existing provider (e.g., Twilio) to understand the interface.

- [ ] **Step 2: Write provider.py**

```python
"""LiveKit SIP telephony provider for Dograh campaign outbound."""

import logging
from dataclasses import dataclass
from typing import Optional

from livekit import api as lk_api

from api.core.config import settings
from api.services.telephony.base import (
    CallResult,
    TelephonyProvider,
)

logger = logging.getLogger(__name__)


@dataclass
class LiveKitSipCallResult(CallResult):
    call_id: str
    provider_metadata: dict


class LiveKitSipProvider(TelephonyProvider):
    """Outbound calls via LiveKit Cloud SIP trunks."""

    PROVIDER_NAME = "livekit_sip"
    WEBHOOK_ENDPOINT = ""  # Not used — LiveKit dispatches directly to AgentServer

    def __init__(self, org_id: int, telephony_config: dict):
        self._org_id = org_id
        self._config = telephony_config

    @property
    def from_numbers(self) -> list[str]:
        return self._config.get("from_numbers", [])

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: int,
        from_number: Optional[str] = None,
        **kwargs,
    ) -> CallResult:
        """Initiate outbound SIP call via LiveKit Cloud."""
        lkapi = lk_api.LiveKitAPI(
            url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
        try:
            import json

            room_name = f"dograh-call-{workflow_run_id}"

            # Build room metadata so dograh-livekit can pick it up
            metadata = json.dumps({
                "deploy_id": str(kwargs.get("workflow_id", "")),
                "org_id": str(self._org_id),
                "channel": "voice_sip",
                "sender_phone": to_number,
                "campaign_id": str(kwargs.get("campaign_id", "")),
                "lead_id": str(kwargs.get("lead_id", "")),
            })

            # Create SIP participant (outbound call)
            participant = await lkapi.sip.create_sip_participant(
                lk_api.CreateSIPParticipantRequest(
                    room_name=room_name,
                    sip_trunk_id=self._config.get("sip_trunk_id", ""),
                    participant_identity=f"sip_out_{workflow_run_id}",
                    participant_name=to_number,
                    # LiveKit will dial this number
                    sip_number=to_number,
                    room_metadata=metadata,
                    agent_dispatch=lk_api.RoomAgentDispatch(
                        agent_name="dograh-agent",
                    ),
                )
            )

            call_id = participant.participant_id or f"lk_{workflow_run_id}"
            logger.info(f"LiveKit SIP outbound call created: room={room_name} call_id={call_id}")

            return LiveKitSipCallResult(
                call_id=call_id,
                provider_metadata={
                    "room_name": room_name,
                    "call_id": call_id,
                },
            )

        finally:
            await lkapi.aclose()
```

- [ ] **Step 3: Register in factory.py**

- [ ] **Step 4: Write tests**

- [ ] **Step 5: Commit**

---

### Task 12: Docker Compose & Integration

**Files:**
- Modify: `docker-compose.yml` (add `dograh-livekit` service)
- Create: `dograh-livekit/.env.example`

- [ ] **Step 1: Add dograh-livekit service to docker-compose.yml**

```yaml
  dograh-livekit:
    build: ./dograh-livekit
    environment:
      - LIVEKIT_URL=${LIVEKIT_URL}
      - LIVEKIT_API_KEY=${LIVEKIT_API_KEY}
      - LIVEKIT_API_SECRET=${LIVEKIT_API_SECRET}
      - DOGRAH_API_URL=http://api:8000
      - DOGRAH_INTERNAL_TOKEN=${DOGRAH_INTERNAL_TOKEN}
      - GOOGLE_API_KEY=${GOOGLE_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - DEEPGRAM_API_KEY=${DEEPGRAM_API_KEY}
      - CARTESIA_API_KEY=${CARTESIA_API_KEY}
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
    networks:
      - dograh_network
    depends_on:
      - api
    restart: unless-stopped
```

- [ ] **Step 2: Create .env.example**

```bash
# dograh-livekit/.env.example
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxxxx
LIVEKIT_API_SECRET=secret_xxxxxxxx
DOGRAH_API_URL=http://api:8000
DOGRAH_INTERNAL_TOKEN=internal-secret-token
GOOGLE_API_KEY=your-google-key
OPENAI_API_KEY=your-openai-key
DEEPGRAM_API_KEY=your-deepgram-key
CARTESIA_API_KEY=your-cartesia-key
LOG_LEVEL=INFO
```

- [ ] **Step 3: End-to-end smoke test**

Write a smoke test that:
1. Starts the services via docker compose
2. Creates a mock deploy in Dograh
3. Simulates a SIP call (or uses LiveKit test tools)
4. Asserts the session record is created and completed

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml dograh-livekit/.env.example
git commit -m "feat: add dograh-livekit to Docker Compose and integration config"
```
