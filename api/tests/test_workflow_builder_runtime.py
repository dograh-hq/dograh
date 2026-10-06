import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from api.enums import OrganizationConfigurationKey
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services import mps_service_key_client as mps_client_module
from api.services.configuration.ai_model_configuration import (
    ResolvedAIModelConfiguration,
)
from api.services.configuration.registry import DograhLLMService, OpenAILLMService
from api.services.workflow.builder_runtime.agent import (
    build_agent,
    builder_instructions,
)
from api.services.workflow.builder_runtime.model import BuilderChatModel
from api.services.workflow.builder_runtime.schemas import BuilderStep, validate_answers
from api.services.workflow.builder_runtime.service import run_step
from api.tests.support.workflow_authoring import BRIEF, READY_REVIEW


class ScriptedModel(BuilderChatModel):
    replies: list[AIMessage] = Field(default_factory=list)
    seen: list = Field(default_factory=list)
    seen_tools: list = Field(default_factory=list)

    async def _agenerate(self, messages, **kwargs):
        self.seen.append(messages)
        self.seen_tools.append(kwargs.get("tools", []))
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def tool_call(name, args, call_id):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


async def approve_plan(graph, config, session_id, current):
    pending = current["pending"][0]
    assert pending["kind"] == "plan_approval"
    return await run_step(
        graph,
        config,
        session_id,
        BuilderStep(
            request_id=uuid4(),
            checkpoint=current["checkpoint"],
            resume={
                pending["id"]: {
                    "decision": "approve",
                    "brief_revision": pending["brief_revision"],
                }
            },
        ),
    )


@pytest.mark.asyncio
async def test_unavailable_tools_are_rejected_even_if_the_model_calls_them():
    forbidden = {
        "ls": {"path": "/"},
        "glob": {"pattern": "*"},
        "grep": {"pattern": "instructions"},
        "read_file": {"file_path": "/instructions.md"},
        "write_file": {"file_path": "/test.md", "content": "test"},
        "edit_file": {"file_path": "/test.md", "old_string": "a", "new_string": "b"},
        "delete": {"file_path": "/test.md"},
        "execute": {"command": "pwd"},
        "task": {"description": "Read files", "subagent_type": "general-purpose"},
        "write_todos": {"todos": []},
        "list_docs": {},
        "read_doc": {"path": "README.md"},
        "search_docs": {"query": "Dograh"},
        "read_artifact": {"id": "example"},
        "create_workflow": {},
    }
    model = ScriptedModel(
        organization_id=12,
        user_id="3",
        replies=[
            *[tool_call(name, args, name) for name, args in forbidden.items()],
            AIMessage(content="Please provide the business information to include."),
        ],
    )
    # Even an expanded MCP catalog cannot introduce these tools into the graph.
    specs = [
        {"name": name, "inputSchema": {"type": "object", "properties": {}}}
        for name in ["list_node_types", *forbidden]
    ]
    session_id = uuid4()
    graph = build_agent(model, InMemorySaver(), specs)
    config = {"configurable": {"thread_id": str(session_id)}}
    result = await run_step(
        graph,
        config,
        session_id,
        BuilderStep(request_id=uuid4(), message="Build an agent"),
    )
    assert not result["pending"]
    assert not result["can_continue"]
    for tools in model.seen_tools:
        assert {item["function"]["name"] for item in tools} == {
            "ask_questions",
            "submit_brief",
        }
    responses = {
        message.tool_call_id: message
        for message in model.seen[-1]
        if isinstance(message, ToolMessage)
    }
    assert set(responses) == set(forbidden)
    assert all(message.status == "error" for message in responses.values())
    assert "not a valid tool" in responses["read_file"].content
    assert not (await graph.aget_state(config)).values.get("files")


@pytest.mark.asyncio
async def test_large_tool_results_remain_readable_without_file_tools():
    model = ScriptedModel(
        organization_id=12,
        user_id="3",
        replies=[
            tool_call("submit_brief", {"brief": BRIEF}, "brief"),
            tool_call("get_voice_prompting_guide", {}, "guide"),
            tool_call(
                "request_clarification",
                {"reason": "What greeting should we use?"},
                "clarify",
            ),
            AIMessage(content="What greeting should we use?"),
        ],
    )
    specs = [
        {
            "name": "get_voice_prompting_guide",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]
    session_id = uuid4()
    config = {"configurable": {"thread_id": str(session_id)}}
    graph = build_agent(model, InMemorySaver(), specs)
    first = await run_step(
        graph,
        config,
        session_id,
        BuilderStep(request_id=uuid4(), message="Build an agent"),
    )
    first = await approve_plan(graph, config, session_id, first)
    content = (
        "Full guidance. " * 7000
    )  # Exceeds the default tool-result offload threshold.
    await run_step(
        graph,
        config,
        session_id,
        BuilderStep(
            request_id=uuid4(),
            checkpoint=first["checkpoint"],
            resume={first["pending"][0]["id"]: {"content": content}},
        ),
    )
    response = next(
        message
        for message in model.seen[-2]
        if isinstance(message, ToolMessage) and message.tool_call_id == "guide"
    )
    assert content in response.content
    assert not (await graph.aget_state(config)).values.get("files")


@pytest.mark.asyncio
async def test_questions_mcp_proposal_and_replay_survive_rebuilding_agent():
    question = {
        "id": "direction",
        "title": "Inbound or outbound?",
        "kind": "single",
        "options": ["Inbound", "Outbound"],
        "allow_custom": False,
    }
    model = ScriptedModel(
        model_name="test",
        organization_id=12,
        user_id="3",
        replies=[
            tool_call("ask_questions", {"questions": [question]}, "question"),
            tool_call("submit_brief", {"brief": BRIEF}, "brief"),
            tool_call("list_node_types", {}, "catalog"),
            tool_call(
                "propose_workflow",
                {
                    "candidate": {
                        "code": "workflow source",
                        "summary": "An inbound receptionist",
                    }
                },
                "proposal",
            ),
            tool_call("submit_review", {"review": READY_REVIEW}, "review"),
        ],
    )
    saver = InMemorySaver()
    specs = [
        {
            "name": "list_node_types",
            "description": "List node types",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]
    session_id = uuid4()
    config = {"configurable": {"thread_id": str(session_id)}}
    graph = build_agent(model, saver, specs)
    first_request = BuilderStep(request_id=uuid4(), message="Build a receptionist")
    first = await run_step(graph, config, session_id, first_request)
    assert first["pending"][0]["kind"] == "questions"
    assert len(model.replies) == 4
    # Idempotent replay does not invoke the model again.
    assert await run_step(graph, config, session_id, first_request) == first
    # A fresh graph represents a different worker restoring persisted state.
    graph = build_agent(model, saver, specs)
    assert (await run_step(graph, config, session_id, None))["pending"] == first[
        "pending"
    ]
    answer_id = first["pending"][0]["id"]
    with pytest.raises(HTTPException) as invalid:
        await run_step(
            graph,
            config,
            session_id,
            BuilderStep(
                request_id=uuid4(),
                checkpoint=first["checkpoint"],
                resume={answer_id: {"direction": ["Invalid"]}},
            ),
        )
    assert invalid.value.status_code == 422
    second = await run_step(
        graph,
        config,
        session_id,
        BuilderStep(
            request_id=uuid4(),
            checkpoint=first["checkpoint"],
            resume={answer_id: {"direction": ["Inbound"]}},
        ),
    )
    second = await approve_plan(graph, config, session_id, second)
    assert second["pending"][0]["tool_name"] == "list_node_types"
    third = await run_step(
        graph,
        config,
        session_id,
        BuilderStep(
            request_id=uuid4(),
            checkpoint=second["checkpoint"],
            resume={second["pending"][0]["id"]: {"node_types": []}},
        ),
    )
    assert third["pending"][0]["tool_name"] == "preview_workflow"
    preview = {
        "valid": True,
        "name": "Receptionist",
        "code": "workflow source",
        "workflow": {"nodes": [], "edges": []},
    }
    final = await run_step(
        graph,
        config,
        session_id,
        BuilderStep(
            request_id=uuid4(),
            checkpoint=third["checkpoint"],
            resume={third["pending"][0]["id"]: preview},
        ),
    )
    assert final["proposal"]["name"] == "Receptionist"
    assert not final["can_continue"]
    assert "save a draft" in final["messages"][-1]["content"]
    assert any(
        builder_instructions() in str(message.content)
        for message in model.seen[0]
        if message.type == "system"
    )


@pytest.mark.asyncio
async def test_stale_checkpoint_and_other_owner_do_not_modify_thread():
    saver = InMemorySaver()
    model = ScriptedModel(
        model_name="test",
        organization_id=12,
        user_id="3",
        replies=[AIMessage(content="Hello")],
    )
    graph = build_agent(model, saver, [])
    session_id = uuid4()
    config = {"configurable": {"thread_id": f"12:3:{session_id}"}}
    await run_step(
        graph, config, session_id, BuilderStep(request_id=uuid4(), message="Hi")
    )
    with pytest.raises(HTTPException) as stale:
        await run_step(
            graph,
            config,
            session_id,
            BuilderStep(request_id=uuid4(), message="Overwrite", checkpoint=None),
        )
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as missing:
        await run_step(
            graph,
            {"configurable": {"thread_id": f"13:3:{session_id}"}},
            session_id,
            None,
        )
    assert missing.value.status_code == 404


def test_multiselect_and_custom_answers():
    questions = [
        {
            "id": "fields",
            "title": "Collect what?",
            "kind": "multiple",
            "options": ["Name", "Email"],
        }
    ]
    answers = {"fields": ["Name", "Email", "Preferred time"]}
    assert validate_answers(questions, answers) == answers
    with pytest.raises(ValueError):
        validate_answers(questions, {"fields": []})


@pytest.fixture
def managed_builder_config(monkeypatch):
    from api.services.configuration import ai_model_configuration
    from api.services.workflow.builder_runtime import model as adapter

    config = AsyncMock(
        return_value=SimpleNamespace(
            value={"mode": "dograh", "dograh": {"api_key": "org-service-key"}},
            last_validated_at=None,
        )
    )
    minted = AsyncMock(return_value={"correlation_id": "builder-correlation"})
    monkeypatch.setattr(ai_model_configuration.db_client, "get_configuration", config)
    monkeypatch.setattr(adapter.mps_service_key_client, "create_correlation_id", minted)
    return config, minted


@pytest.mark.asyncio
async def test_model_adapter_preserves_reasoning_and_provider_tool_metadata(
    managed_builder_config,
):
    raw_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "list_node_types", "arguments": "{}"},
        "provider_specific_fields": {"thought_signature": "provider-signature"},
    }
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "Provider reasoning",
                    "tool_calls": [raw_call],
                }
            }
        ]
    }
    model = BuilderChatModel(organization_id=2, user_id="3")
    from api.services.workflow.builder_runtime import model as adapter

    complete = AsyncMock(
        side_effect=[
            response,
            {"choices": [{"message": {"content": "Ready"}}]},
        ]
    )
    with patch.object(
        adapter.mps_service_key_client, "create_chat_completion", complete
    ):
        first = await model.ainvoke([HumanMessage(content="Build an agent")])
        await model.ainvoke(
            [
                HumanMessage(content="Build an agent"),
                first,
                ToolMessage(content="[]", tool_call_id="call-1"),
            ]
        )
    assert complete.await_count == 2
    payload = complete.await_args.kwargs
    assert payload["service_key"] == "org-service-key"
    assert payload["model"] == "workflow_builder"
    assert payload["metadata"]["usage_context"] == "workflow_builder"
    assert payload["metadata"]["dograh_organization_id"] == "2"
    assert "correlation_id" not in payload["metadata"]
    assert "mps_billing_version" not in payload["metadata"]
    config, minted = managed_builder_config
    assert (
        config.await_args_list
        == [call(2, OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value)] * 2
    )
    minted.assert_not_awaited()
    assert "org-service-key" not in model.model_dump_json()
    assert "org-service-key" not in str(payload["messages"])
    wire = payload["messages"]
    assert wire[1]["reasoning_content"] == "Provider reasoning"
    assert wire[1]["tool_calls"] == [raw_call]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "llm",
    [
        None,
        DograhLLMService(api_key=""),
        DograhLLMService(api_key="   "),
        OpenAILLMService(api_key="byok-secret"),
    ],
    ids=["missing", "empty", "blank", "byok"],
)
async def test_model_adapter_requires_configured_dograh_key(
    monkeypatch, managed_builder_config, llm
):
    from api.services.workflow.builder_runtime import model as adapter

    monkeypatch.setattr(
        adapter,
        "get_resolved_ai_model_configuration",
        AsyncMock(
            return_value=ResolvedAIModelConfiguration(
                effective=EffectiveAIModelConfiguration(llm=llm),
                source="organization_v2",
            )
        ),
    )
    # A leftover process-wide key cannot enable an unconfigured organization.
    monkeypatch.setenv("WORKFLOW_BUILDER_MPS_API_KEY", "unrelated-server-key")
    model = BuilderChatModel(organization_id=2, user_id="3")
    with (
        patch.object(mps_client_module.httpx, "AsyncClient") as client,
        pytest.raises(HTTPException) as error,
    ):
        await model.ainvoke([HumanMessage(content="Hello")])
    assert error.value.status_code == 400
    assert "only supported with a Dograh Service Key" in error.value.detail
    assert "/model-configurations" in error.value.detail
    managed_builder_config[1].assert_not_awaited()
    client.assert_not_called()


@pytest.mark.asyncio
async def test_model_adapter_sanitizes_upstream_errors(managed_builder_config):
    model = BuilderChatModel(organization_id=2, user_id="3")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                401, json={"detail": "sensitive provider error"}
            )
        )
    )
    with (
        patch.object(mps_client_module.httpx, "AsyncClient", return_value=client),
        pytest.raises(HTTPException) as error,
    ):
        await model.ainvoke([HumanMessage(content="Hello")])
    assert error.value.status_code == 503
    assert "sensitive" not in error.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_request", [True, False], ids=["unavailable", "missing-id"]
)
async def test_model_adapter_requires_correlation_before_inference(
    managed_builder_config, failed_request
):
    _, minted = managed_builder_config
    if failed_request:
        minted.side_effect = httpx.ConnectError("sensitive upstream error")
    else:
        minted.return_value = {}
    with (
        patch.object(mps_client_module.httpx, "AsyncClient") as client,
        pytest.raises(HTTPException) as error,
    ):
        await BuilderChatModel(organization_id=2, user_id="3").ainvoke(
            [HumanMessage(content="Hello")]
        )
    assert error.value.status_code == 503
    assert "sensitive" not in error.value.detail
    client.assert_not_called()


@pytest.mark.asyncio
async def test_model_adapter_isolates_org_keys_and_refreshes_configuration(
    monkeypatch, managed_builder_config
):
    config, minted = managed_builder_config
    keys = {2: "org-two-key", 4: "org-four-key"}

    async def lookup(org_id, config_key):
        await asyncio.sleep(0)
        assert config_key == OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value
        return SimpleNamespace(
            value={"mode": "dograh", "dograh": {"api_key": keys[org_id]}},
            last_validated_at=None,
        )

    async def mint(*, service_key):
        await asyncio.sleep(0)
        return {"correlation_id": f"correlation-for-{service_key}"}

    config.side_effect = lookup
    minted.side_effect = mint
    requests = []

    def complete(request):
        import json

        payload = json.loads(request.content)
        org_id = int(payload["metadata"]["dograh_organization_id"])
        assert request.headers["Authorization"] == f"Bearer {keys[org_id]}"
        assert (
            payload["metadata"]["correlation_id"] == f"correlation-for-{keys[org_id]}"
        )
        requests.append(org_id)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Hello"}}]}
        )

    actual_client = httpx.AsyncClient
    monkeypatch.setattr(
        mps_client_module.httpx,
        "AsyncClient",
        lambda **kwargs: actual_client(
            transport=httpx.MockTransport(complete), **kwargs
        ),
    )
    model = BuilderChatModel(organization_id=2, user_id="3")
    await asyncio.gather(
        model.ainvoke([HumanMessage(content="Hello")]),
        BuilderChatModel(organization_id=4, user_id="5").ainvoke(
            [HumanMessage(content="Hello")]
        ),
    )
    keys[2] = "rotated-org-two-key"
    await model.ainvoke([HumanMessage(content="Continue")])
    assert sorted(requests) == [2, 2, 4]


@pytest.mark.asyncio
async def test_postgres_checkpoint_survives_connections_and_excludes_concurrent_writers(
    test_engine,
    monkeypatch,
):
    from api.db import db_client
    from api.db.workflow_builder import BuilderBusy

    monkeypatch.setattr(db_client, "engine", test_engine)
    model = ScriptedModel(
        model_name="test",
        organization_id=1,
        user_id="1",
        replies=[
            tool_call(
                "ask_questions",
                {
                    "questions": [
                        {"id": "goal", "title": "What should it do?", "kind": "text"}
                    ]
                },
                "ask",
            ),
            tool_call("submit_brief", {"brief": BRIEF}, "brief"),
            tool_call(
                "propose_workflow",
                {"candidate": {"code": "source", "summary": "Receptionist"}},
                "candidate",
            ),
            tool_call("submit_review", {"review": READY_REVIEW}, "review"),
        ],
    )
    session_id = uuid4()
    thread = f"workflow-builder:1:1:{session_id}"
    config = {"configurable": {"thread_id": thread}}
    try:
        async with db_client.builder_checkpointer(thread) as saver:
            request = BuilderStep(request_id=uuid4(), message="Build an agent")
            first = await run_step(
                build_agent(model, saver, []), config, session_id, request
            )
            with pytest.raises(BuilderBusy):
                async with db_client.builder_checkpointer(thread):
                    pytest.fail("Second writer acquired the same thread")
        async with db_client.builder_checkpointer(thread) as saver:
            graph = build_agent(model, saver, [])
            restored = await run_step(graph, config, session_id, None)
            assert restored["pending"] == first["pending"]
            assert await run_step(graph, config, session_id, request) == restored
            candidate = await run_step(
                graph,
                config,
                session_id,
                BuilderStep(
                    request_id=uuid4(),
                    checkpoint=restored["checkpoint"],
                    resume={restored["pending"][0]["id"]: {"goal": ["Qualify leads"]}},
                ),
            )
            assert candidate["pending"][0]["kind"] == "plan_approval"
        # Restore the approval across connections before any Builder inference.
        async with db_client.builder_checkpointer(thread) as saver:
            graph = build_agent(model, saver, [])
            restored_plan = await run_step(graph, config, session_id, None)
            assert restored_plan["pending"] == candidate["pending"]
            assert len(model.seen) == 2
            candidate = await approve_plan(graph, config, session_id, restored_plan)
            assert candidate["pending"][0]["tool_name"] == "preview_workflow"
            assert candidate["proposal"] is None
        # Restore the validated-source interrupt across another pooled connection.
        async with db_client.builder_checkpointer(thread) as saver:
            graph = build_agent(model, saver, [])
            final = await run_step(
                graph,
                config,
                session_id,
                BuilderStep(
                    request_id=uuid4(),
                    checkpoint=candidate["checkpoint"],
                    resume={
                        candidate["pending"][0]["id"]: {
                            "valid": True,
                            "name": "Receptionist",
                            "code": "source",
                            "workflow": {"nodes": [], "edges": []},
                        }
                    },
                ),
            )
            assert not final["can_continue"]
            assert final["proposal"]["code"] == "source"
            values = (await graph.aget_state(config)).values
            assert values["review"]["brief_revision"] == values["brief_revision"] == 1
            assert (
                values["plan_messages"]
                and values["build_messages"]
                and values["review_messages"]
            )
            assert any(
                message["content"] == "What should it do?: Qualify leads"
                for message in final["messages"]
            )
        async with db_client.builder_checkpointer(thread) as saver:
            assert (
                await run_step(build_agent(model, saver, []), config, session_id, None)
                == final
            )
    finally:
        async with db_client.builder_checkpointer(thread) as saver:
            await saver.adelete_thread(thread)
