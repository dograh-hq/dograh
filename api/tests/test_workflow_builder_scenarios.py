"""Repeatable scenario regression set; fixtures test the harness, not model quality."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from api.services.workflow.builder_runtime import scenarios
from api.tests.test_workflow_builder import VALID_CODE
from api.tests.test_workflow_builder_runtime import ScriptedModel, tool_call

CASES = json.loads(
    (Path(__file__).parent / "fixtures/builder/scenarios.json").read_text()
)
CODE = VALID_CODE.replace(
    'name: "Welcome",', 'name: "Welcome", tool_uuids: ["calculator"],'
)


@pytest.fixture
def calculator(monkeypatch):
    tool = SimpleNamespace(tool_uuid="calculator", category="calculator")
    monkeypatch.setattr(
        "api.services.workflow.builder_validation.validate_workflow_tool_name_collisions",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        scenarios.db_client,
        "get_tools_for_organization",
        AsyncMock(return_value=[tool]),
    )
    lookup = AsyncMock(return_value=[tool])
    monkeypatch.setattr(scenarios.db_client, "get_tools_by_uuids", lookup)
    return lookup


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
async def test_scenario_regression_set(case, calculator):
    model = ScriptedModel(
        organization_id=7,
        user_id="3",
        replies=[
            tool_call(reply["tool"], reply["args"], str(i))
            if "tool" in reply
            else AIMessage(content=reply["text"])
            for i, reply in enumerate(case["replies"])
        ],
    )
    result = await scenarios.run_workflow_scenario(
        CODE,
        scenarios.WorkflowScenario.model_validate(case["scenario"]),
        7,
        "3",
        model=model,
    )
    assert "passed" in result, result
    assert result["passed"] is case.get("expect_passed", True), result
    assert result["metrics"]["model_calls"] == len(case["replies"])
    assert result["metrics"]["input_characters"] > 0
    calculator.assert_awaited_once_with(["calculator"], 7)
    assert all(message[0].content == "Greet the caller." for message in model.seen)
    if case["name"] == "tool_failure":
        assert "Service unavailable" in model.seen[-1][-1].content


@pytest.mark.asyncio
async def test_transition_uses_production_function_names_and_target_prompt(calculator):
    model = ScriptedModel(
        organization_id=7,
        user_id="3",
        replies=[
            tool_call("continue", {}, "transition"),
            AIMessage(content="What is your name?"),
        ],
    )
    result = await scenarios.run_workflow_scenario(
        CODE,
        scenarios.WorkflowScenario(
            caller_messages=["I am ready"], expected_nodes=["Qualify"]
        ),
        7,
        "3",
        model=model,
    )
    assert result["passed"], result
    assert model.seen[-1][0].content == "Ask for the caller's name."


@pytest.mark.asyncio
async def test_unknown_tool_and_invalid_arguments_fail_closed(calculator):
    for name, args in [("execute_http_tool", {}), ("safe_calculator", {})]:
        model = ScriptedModel(
            organization_id=7, user_id="3", replies=[tool_call(name, args, "bad")]
        )
        result = await scenarios.run_workflow_scenario(
            CODE,
            scenarios.WorkflowScenario(
                caller_messages=["Hello"], expected_nodes=["Welcome"]
            ),
            7,
            "3",
            model=model,
        )
        assert result["passed"] is False
        assert result["failures"]
        assert len(model.seen) == 1


@pytest.mark.asyncio
async def test_no_assertions_is_not_reported_as_a_pass(calculator):
    model = ScriptedModel(
        organization_id=7, user_id="3", replies=[AIMessage(content="Hello")]
    )
    result = await scenarios.run_workflow_scenario(
        CODE, scenarios.WorkflowScenario(caller_messages=["Hello"]), 7, "3", model=model
    )
    assert result["passed"] is None


@pytest.mark.asyncio
async def test_simulation_rejects_unavailable_or_unsupported_tools_before_inference(
    calculator,
):
    model = ScriptedModel(organization_id=7, user_id="3")
    scenario = scenarios.WorkflowScenario(caller_messages=["Hello"])
    calculator.return_value = []
    result = await scenarios.run_workflow_scenario(CODE, scenario, 7, "3", model=model)
    assert "no longer available" in result["error"]
    calculator.return_value = [SimpleNamespace(tool_uuid="calculator", category="mcp")]
    result = await scenarios.run_workflow_scenario(CODE, scenario, 7, "3", model=model)
    assert "runtime test" in result["error"]
    assert not model.seen
