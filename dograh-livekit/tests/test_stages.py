import pytest
import asyncio
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
        stage = CustomStage(stage_config, agent_config)
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

    def test_no_routes_still_works(self, agent_config):
        stage_config = {
            "id": "intent",
            "type": "identify_intent",
            "label": "Simple Intent",
            "instructions": "Find intent",
            "routes": {},
        }
        stage = IdentifyIntentStage(stage_config, agent_config)
        assert "record_intent" in stage.instructions.lower()
