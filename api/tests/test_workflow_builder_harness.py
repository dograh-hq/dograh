import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from api.services.workflow.builder_runtime.agent import local_tools
from api.services.workflow.builder_runtime.context import (
    bounded_messages,
    recent_conversation,
)
from api.services.workflow.builder_runtime.tool_schemas import tool_request_schema
from api.services.workflow.builder_runtime.working_copy import (
    edit_source,
    source_revision,
)
from api.tests.test_workflow_builder import VALID_CODE
from api.tests.test_workflow_builder_runtime import (  # noqa: F401
    managed_builder_config as _managed_builder_config,
)
from api.tests.test_workflow_builder_runtime import (
    tool_call,
)
from api.tests.test_workflow_builder_stages import Conversation


def test_editor_tool_schemas_stay_compact_and_category_schemas_are_authoritative():
    tools = [convert_to_openai_tool(t) for t in local_tools("build", iteration=True)]
    assert len(json.dumps(tools)) < 15_000
    calculator = tool_request_schema("calculator")["schema"]
    assert "CalculatorToolDefinition" in calculator["$defs"]
    assert "HttpApiToolDefinition" not in calculator["$defs"]
    assert {"name", "definition"} <= set(calculator["required"])
    assert "required" not in tool_request_schema("http_api", "update")["schema"]


@pytest.mark.parametrize(
    "old,new", [("missing", "new"), ("a", "new"), ("", "new"), ("a a", "a a")]
)
def test_exact_edits_reject_missing_ambiguous_and_noop_matches(old, new):
    assert "error" in edit_source("a a", source_revision("a a"), old, new)


def test_stale_revision_cannot_overwrite_newer_source():
    result = edit_source("new source", source_revision("old source"), "new", "other")
    assert "error" in result
    assert result["revision"] == source_revision("new source")


@pytest.mark.asyncio
async def test_exact_edit_validates_then_finishes_without_resending_source():
    edited = VALID_CODE.replace("Greet the caller.", "Welcome the caller warmly.")
    revision = source_revision(edited)
    conversation = Conversation(
        [
            tool_call(
                "edit_workflow",
                {
                    "expected_revision": source_revision(VALID_CODE),
                    "old_text": "Greet the caller.",
                    "new_text": "Welcome the caller warmly.",
                },
                "edit",
            ),
            tool_call(
                "finish_workflow",
                {"expected_revision": revision, "summary": "Premature finish"},
                "early",
            ),
            tool_call("validate_workflow", {"expected_revision": revision}, "validate"),
            tool_call(
                "finish_workflow",
                {"expected_revision": revision, "summary": "Updated the greeting."},
                "finish",
            ),
        ]
    )
    current = await conversation.step(
        message="Make the greeting warmer",
        current_workflow={"id": 41, "revision": "saved", "code": VALID_CODE},
    )
    assert current["proposal"] is None
    assert current["pending"][0]["arguments"]["code"] == edited
    state = await conversation.state()
    assert state["working_code"] == edited
    assert any("Validate this" in str(m.content) for m in state["build_messages"])
    final = await conversation.resume(
        {
            "valid": True,
            "name": "Receptionist",
            "code": edited,
            "workflow": {"nodes": [], "edges": []},
        }
    )
    assert final["proposal"]["code"] == edited
    assert final["proposal"]["summary"] == "Updated the greeting."
    assert not conversation.model.replies


@pytest.mark.asyncio
async def test_editor_can_explain_without_a_proposal_or_spurious_retry():
    conversation = Conversation(
        [AIMessage(content="This agent greets callers and collects their name.")]
    )
    result = await conversation.step(
        message="What does this agent do?",
        current_workflow={"id": 41, "revision": "saved", "code": VALID_CODE},
    )
    assert result["proposal"] is None
    assert not result["can_continue"]
    assert result["messages"][-1]["content"].startswith("This agent")
    assert len(conversation.model.seen) == 1


@pytest.mark.asyncio
async def test_invalid_tool_definition_is_repaired_before_approval():
    conversation = Conversation(
        [
            tool_call(
                "propose_tool",
                {
                    "tool": {"name": "Invalid", "definition": {"type": "http_api"}},
                    "plain_language": "Invalid",
                },
                "invalid",
            ),
            tool_call(
                "propose_tool",
                {
                    "tool": {
                        "name": "Calculator",
                        "definition": {"type": "calculator"},
                    },
                    "plain_language": "Arithmetic",
                },
                "valid",
            ),
        ]
    )
    result = await conversation.step(
        message="Add a calculator",
        current_workflow={"id": 41, "revision": "saved", "code": VALID_CODE},
    )
    assert len(conversation.model.seen) == 2
    assert result["pending"][0]["tool"]["name"] == "Calculator"
    assert "Invalid tool definition" in conversation.model.seen[-1][-1].content


@pytest.mark.asyncio
async def test_oversized_tool_results_are_durable_and_pageable_across_edits():
    output = {"content": "x" * 20_000}
    conversation = Conversation(
        [
            tool_call("get_voice_prompting_guide", {}, "guide-result"),
            AIMessage(content="I read the guide."),
            tool_call(
                "read_tool_result",
                {"result_id": "guide-result", "offset": 8000},
                "page",
            ),
            AIMessage(content="The requested guidance is available."),
        ],
        [
            {
                "name": "get_voice_prompting_guide",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )
    await conversation.step(
        message="Read the guide",
        current_workflow={"id": 41, "revision": "saved", "code": VALID_CODE},
    )
    await conversation.resume(output)
    assert "truncated" in conversation.model.seen[-1][-1].content
    result_id = json.loads(conversation.model.seen[-1][-1].content)["result_id"]
    assert (
        json.loads((await conversation.state())["tool_results"][result_id]["content"])
        == output
    )
    conversation.model.replies[0].tool_calls[0]["args"]["result_id"] = result_id
    await conversation.step(message="What does the rest say?")
    assert "guide-result" in conversation.model.seen[-1][1].content
    page = next(m for m in conversation.model.seen[-1] if isinstance(m, ToolMessage))
    assert len(json.loads(page.content)["content"]) == 8000


def test_context_eviction_keeps_tool_exchanges_whole_and_recent_input():
    messages = [HumanMessage(content="current source")]
    for index in range(8):
        messages.extend(
            [
                tool_call("list_tools", {}, str(index)),
                ToolMessage(content="x" * 1000, tool_call_id=str(index)),
            ]
        )
    bounded = bounded_messages(messages, budget=3000)
    assert len(bounded) < len(messages)
    ids = {c["id"] for m in bounded for c in getattr(m, "tool_calls", [])}
    assert all(m.tool_call_id in ids for m in bounded if m.type == "tool")
    recent = recent_conversation(
        [
            {"role": "user", "content": "x" * 9000},
            {"role": "user", "content": "latest request"},
        ]
    )
    assert recent[-1]["content"] == "latest request"


@pytest.mark.asyncio
async def test_repair_limit_preserves_source_and_returns_to_user():
    repair = AIMessage(
        content="",
        additional_kwargs={"builder_repair": "Truncated output. Use smaller edits."},
    )
    conversation = Conversation([repair, repair, repair])
    result = await conversation.step(
        message="Edit",
        current_workflow={"id": 41, "revision": "saved", "code": VALID_CODE},
    )
    assert not result["can_continue"]
    assert result["proposal"] is None
    assert (await conversation.state())["working_code"] == VALID_CODE
    assert len(conversation.model.seen) == 3


@pytest.mark.asyncio
@pytest.mark.usefixtures("_managed_builder_config")
async def test_model_repair_discards_entire_malformed_tool_batch(monkeypatch):
    from unittest.mock import AsyncMock

    from api.services.workflow.builder_runtime import model as adapter

    response = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "first",
                            "function": {"name": "list_tools", "arguments": "{}"},
                        },
                        {
                            "id": "bad",
                            "function": {
                                "name": "edit_workflow",
                                "arguments": '{"partial":',
                            },
                        },
                    ],
                }
            }
        ]
    }
    monkeypatch.setattr(
        adapter.mps_service_key_client,
        "create_chat_completion",
        AsyncMock(return_value=response),
    )
    reply = await adapter.BuilderChatModel(organization_id=7, user_id="3").ainvoke(
        [HumanMessage(content="Edit")]
    )
    assert reply.additional_kwargs["builder_repair"]
    assert not reply.tool_calls
    assert "mps_message" not in reply.additional_kwargs


@pytest.mark.asyncio
@pytest.mark.usefixtures("_managed_builder_config")
async def test_output_limit_and_transient_inference_recovery(monkeypatch):
    from unittest.mock import AsyncMock

    import httpx

    from api.services.workflow.builder_runtime import model as adapter

    response = {
        "choices": [{"finish_reason": "length", "message": {"content": "{truncated"}}]
    }
    complete = AsyncMock(side_effect=[httpx.ConnectError("unavailable"), response])
    monkeypatch.setattr(
        adapter.mps_service_key_client, "create_chat_completion", complete
    )
    monkeypatch.setattr(adapter.asyncio, "sleep", AsyncMock())
    reply = await adapter.BuilderChatModel(organization_id=7, user_id="3").ainvoke(
        [HumanMessage(content="Edit")]
    )
    assert complete.await_count == 2
    assert "output limit" in reply.additional_kwargs["builder_repair"]
    assert not reply.tool_calls
