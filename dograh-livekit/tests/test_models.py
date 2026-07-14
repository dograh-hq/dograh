import pytest
from app.models import (
    RuntimeConfig,
    NodeData,
    EdgeData,
    ToolDefinition,
    ModelConfig,
    STTConfig,
    TTSConfig,
    WorkflowGraph,
    RFNode,
    RFEdge,
    Position,
)


class TestRuntimeConfig:
    def test_valid_minimal_config(self):
        data = {
            "workflow_id": 123,
            "org_id": "org_456",
            "agent_id": "ag_789",
            "workflow_graph": {
                "nodes": [
                    {
                        "id": "n1",
                        "type": "startCall",
                        "position": {"x": 0, "y": 0},
                        "data": {"name": "Start", "prompt": "Hello", "greeting": "Hi there"},
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
        assert config.workflow_id == 123
        assert len(config.workflow_graph.nodes) == 2

    def test_invalid_missing_org_id(self):
        with pytest.raises(ValueError):
            RuntimeConfig(workflow_id=123, workflow_graph={"nodes": [], "edges": []})

    def test_tool_definition_parsing(self):
        tool = ToolDefinition(
            name="search_knowledge",
            type="kb_search",
            config={"kb_refs": ["kb_1", "kb_2"]},
        )
        assert tool.name == "search_knowledge"
        assert tool.config["kb_refs"] == ["kb_1", "kb_2"]

    def test_extra_fields_allowed(self):
        """RuntimeConfig should accept extra fields from Dograh API."""
        data = {
            "workflow_id": 123,
            "org_id": "org_456",
            "workflow_graph": {"nodes": [], "edges": []},
            "custom_field": "should be allowed",
        }
        config = RuntimeConfig(**data)
        assert config.custom_field == "should be allowed"
