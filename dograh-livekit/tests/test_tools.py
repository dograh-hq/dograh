import pytest
from unittest.mock import MagicMock
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
        proxy = MockAgentProxy({"kb_refs": ["kb_1"], "org_id": "org_1", "deploy_id": "dp_1"})
        tools = build_tools(proxy)
        assert len(tools) >= 1
        for tool in tools:
            assert callable(tool)

    def test_no_kb_tool_without_refs(self):
        proxy = MockAgentProxy({"kb_refs": [], "org_id": "org_1", "deploy_id": "dp_1"})
        tools = build_tools(proxy)
        assert len(tools) == 0

    def test_registry_has_search_knowledge(self):
        assert "search_knowledge" in TOOL_REGISTRY
