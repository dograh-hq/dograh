import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastmcp import Client
from httpx import ASGITransport, AsyncClient

from api.mcp_server.auth import builder_mcp_user
from api.mcp_server.server import mcp
from api.schemas.workflow_builder import BuilderTurnRequest
from api.services.workflow.builder import (
    builder_turn,
    call_builder_tool,
    save_builder_draft,
)
from api.services.workflow.builder_validation import preview_workflow_code
from api.tests.support.workflow_authoring import BRIEF, READY_REVIEW
from api.tests.test_workflow_builder_tracing import _captured_traces  # noqa: F401

VALID_CODE = """import { Workflow } from "@dograh/sdk";
import { startCall, agentNode, endCall } from "@dograh/sdk/typed";
const wf = new Workflow({ name: "Receptionist" });
const start = wf.addTyped(startCall({ name: "Welcome", prompt: "Greet the caller." }));
const qualify = wf.addTyped(agentNode({ name: "Qualify", prompt: "Ask for the caller's name." }));
const end = wf.addTyped(endCall({ name: "Goodbye", prompt: "Say goodbye." }));
wf.edge(start, qualify, { label: "Continue", condition: "The caller is ready" });
wf.edge(qualify, end, { label: "Done", condition: "The name was collected" });"""


@pytest.mark.asyncio
async def test_preview_uses_real_parser_and_does_not_save():
    with (
        patch(
            "api.services.workflow.builder_validation.validate_workflow_tool_name_collisions",
            AsyncMock(return_value=[]),
        ),
        patch(
            "api.services.workflow.builder_validation.db_client.create_workflow",
            AsyncMock(),
        ) as create,
    ):
        result = await preview_workflow_code(VALID_CODE, 17)
    assert result["valid"], result
    assert len(result["workflow"]["nodes"]) == 3
    assert result["name"] == "Receptionist"
    create.assert_not_called()


@pytest.mark.asyncio
async def test_preview_rejects_executable_code_and_foreign_references():
    assert not (await preview_workflow_code("process.exit(0);", 17))["valid"]
    code = VALID_CODE.replace(
        'name: "Qualify",', 'name: "Qualify", tool_uuids: ["other-org-tool"],'
    )
    with (
        patch(
            "api.services.workflow.builder_validation.validate_workflow_tool_name_collisions",
            AsyncMock(return_value=[]),
        ),
        patch(
            "api.services.workflow.builder_validation.db_client.get_tools_for_organization",
            AsyncMock(return_value=[]),
        ) as catalog,
    ):
        result = await preview_workflow_code(code, 17)
    assert not result["valid"]
    assert "reference" in result["error"]
    catalog.assert_awaited_once_with(organization_id=17)


@pytest.mark.asyncio
async def test_mcp_relay_keeps_concurrent_organizations_separate():
    async def catalog(organization_id, **kwargs):
        await asyncio.sleep(0)
        return [
            SimpleNamespace(
                tool_uuid=f"org-{organization_id}",
                name="Test",
                description="",
                category="api",
            )
        ]

    async def call(org):
        token = builder_mcp_user.set(
            SimpleNamespace(id=org, selected_organization_id=org)
        )
        try:
            async with Client(mcp) as client:
                return await call_builder_tool(client, "list_tools", {})
        finally:
            builder_mcp_user.reset(token)

    with patch(
        "api.mcp_server.tools.catalog.db_client.get_tools_for_organization",
        side_effect=catalog,
    ):
        first, second = await asyncio.gather(call(1), call(2))
    assert first[0]["tool_uuid"] == "org-1"
    assert second[0]["tool_uuid"] == "org-2"
    assert builder_mcp_user.get() is None


@pytest.mark.asyncio
async def test_browser_cannot_supply_mcp_results_or_call_write_tools():
    client = AsyncMock()
    assert "error" in await call_builder_tool(
        client, "create_workflow", {"code": VALID_CODE}
    )
    client.call_tool.assert_not_called()
    user = SimpleNamespace(id=1, selected_organization_id=2)
    with patch(
        "api.services.workflow.builder.local_step",
        AsyncMock(return_value={"pending": [{"id": "tool", "kind": "mcp"}]}),
    ) as upstream:
        events = [
            event
            async for event in builder_turn(
                user,
                uuid4(),
                BuilderTurnRequest(
                    request_id=uuid4(), answers={"tool": {"valid": ["true"]}}
                ),
            )
        ]
    assert events[-1]["type"] == "error"
    assert "questions" in events[-1]["message"]
    assert upstream.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,interrupt_id",
    [("mcp", "pending"), ("questions", "pending"), ("plan_approval", "old-plan")],
)
async def test_browser_cannot_use_approval_to_resume_other_interrupts(
    kind, interrupt_id
):
    user = SimpleNamespace(id=1, selected_organization_id=2)
    with patch(
        "api.services.workflow.builder.local_step",
        AsyncMock(return_value={"pending": [{"id": "pending", "kind": kind}]}),
    ) as upstream:
        events = [
            event
            async for event in builder_turn(
                user,
                uuid4(),
                BuilderTurnRequest(
                    request_id=uuid4(),
                    approval={
                        "interrupt_id": interrupt_id,
                        "decision": "approve",
                        "brief_revision": 1,
                    },
                ),
            )
        ]
    assert events[-1]["type"] == "error"
    assert "No matching plan" in events[-1]["message"]
    assert upstream.await_count == 1


@pytest.mark.asyncio
async def test_browser_cannot_answer_a_plan_as_a_question():
    user = SimpleNamespace(id=1, selected_organization_id=2)
    with patch(
        "api.services.workflow.builder.local_step",
        AsyncMock(return_value={"pending": [{"id": "plan", "kind": "plan_approval"}]}),
    ) as upstream:
        events = [
            event
            async for event in builder_turn(
                user,
                uuid4(),
                BuilderTurnRequest(
                    request_id=uuid4(),
                    answers={"plan": {"decision": ["approve"]}},
                ),
            )
        ]
    assert events[-1]["type"] == "error"
    assert upstream.await_count == 1


@pytest.mark.asyncio
async def test_save_revalidates_and_uses_stable_org_scoped_retry_key():
    user = SimpleNamespace(id=3, selected_organization_id=7)
    session_id = uuid4()
    state = SimpleNamespace(
        checkpoint="current",
        can_continue=False,
        proposal=SimpleNamespace(code=VALID_CODE),
    )
    with (
        patch(
            "api.services.workflow.builder.load_session", AsyncMock(return_value=state)
        ),
        patch(
            "api.services.workflow.builder.preview_workflow_code",
            AsyncMock(
                return_value={
                    "valid": True,
                    "name": "Receptionist",
                    "workflow": {"nodes": [], "edges": []},
                }
            ),
        ) as preview,
        patch(
            "api.services.workflow.builder.db_client.create_builder_draft",
            AsyncMock(return_value=SimpleNamespace(id=41)),
        ) as create,
    ):
        with pytest.raises(HTTPException) as stale:
            await save_builder_draft(user, session_id, "old")
        assert stale.value.status_code == 409
        create.assert_not_called()
        assert await save_builder_draft(user, session_id, "current") == 41
        assert await save_builder_draft(user, session_id, "current") == 41
    assert preview.call_args.args == (VALID_CODE, 7)
    assert create.call_args_list[0] == create.call_args_list[1]
    assert create.call_args.kwargs["organization_id"] == 7
    assert create.call_args.kwargs["user_id"] == 3


@pytest.mark.asyncio
async def test_streaming_route_relays_real_mcp_under_request_identity():
    from api.routes.workflow_builder import router
    from api.services.auth.depends import get_user

    app = FastAPI()
    app.include_router(router)
    user = SimpleNamespace(id=3, selected_organization_id=27)
    app.dependency_overrides[get_user] = lambda: user
    session_id = uuid4()
    initial = {
        "id": str(session_id),
        "checkpoint": "one",
        "messages": [],
        "pending": [
            {"id": "catalog", "kind": "mcp", "tool_name": "list_tools", "arguments": {}}
        ],
        "proposal": None,
        "can_continue": True,
    }
    final = {
        **initial,
        "checkpoint": "two",
        "pending": [],
        "can_continue": False,
        "messages": [{"id": "a", "role": "assistant", "content": "Ready"}],
    }
    with (
        patch(
            "api.services.workflow.builder.local_step",
            AsyncMock(side_effect=[initial, final]),
        ) as upstream,
        patch(
            "api.mcp_server.tools.catalog.db_client.get_tools_for_organization",
            AsyncMock(return_value=[]),
        ) as catalog,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/workflow/builder/{session_id}/turn",
                json={"request_id": str(uuid4()), "message": "Build an agent"},
            )
    assert response.status_code == 200
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1]["type"] == "done"
    catalog.assert_awaited_once_with(organization_id=27, status="active")
    assert upstream.call_args.args[2]["resume"] == {"catalog": []}
    assert builder_mcp_user.get() is None


@pytest.mark.asyncio
async def test_local_builder_routes_inference_and_builds_a_real_preview(
    monkeypatch, captured_traces
):
    """Exercise HTTP -> local agent -> MCP -> preview -> draft with fake inference."""
    import httpx
    from langgraph.checkpoint.memory import InMemorySaver

    from api.routes.workflow_builder import router
    from api.services import mps_service_key_client as mps_client_module
    from api.services.auth.depends import get_user
    from api.services.configuration import ai_model_configuration
    from api.services.workflow.builder_runtime import service as runtime
    from api.services.workflow.builder_runtime.agent import builder_instructions

    saver = InMemorySaver()

    @asynccontextmanager
    async def checkpoints(thread_id):
        yield saver

    monkeypatch.setattr(runtime.db_client, "builder_checkpointer", checkpoints)
    config = AsyncMock(
        return_value=SimpleNamespace(
            value={"mode": "dograh", "dograh": {"api_key": "org-service-key"}},
            last_validated_at=None,
        )
    )
    monkeypatch.setattr(ai_model_configuration.db_client, "get_configuration", config)
    monkeypatch.setattr(
        "api.services.workflow.builder_validation.validate_workflow_tool_name_collisions",
        AsyncMock(return_value=[]),
    )
    create = AsyncMock(return_value=SimpleNamespace(id=92))
    monkeypatch.setattr(
        "api.services.workflow.builder.db_client.create_builder_draft", create
    )
    question = {
        "id": "direction",
        "title": "Inbound or outbound?",
        "kind": "single",
        "options": ["Inbound", "Outbound"],
    }
    replies = [
        ("ask_questions", {"questions": [question]}),
        ("submit_brief", {"brief": BRIEF}),
        ("list_node_types", {}),
        *[
            call
            for node_type in ("startCall", "agentNode", "endCall")
            for call in (
                ("get_node_type", {"name": node_type}),
                (
                    "get_voice_prompting_guide",
                    {"stage": "create", "node_type": node_type},
                ),
            )
        ],
        (
            "propose_workflow",
            {"candidate": {"code": VALID_CODE, "summary": "An inbound receptionist"}},
        ),
        *[
            (
                "get_voice_prompting_guide",
                {"stage": "review", "node_type": node_type},
            )
            for node_type in ("startCall", "agentNode", "endCall")
        ],
        ("submit_review", {"review": READY_REVIEW}),
    ]
    expected_calls = len(replies)
    calls = []
    correlation_requests = []
    trace_headers = []

    def complete(request):
        assert request.headers["Authorization"] == "Bearer org-service-key"
        if request.url.path == "/api/v1/service-keys/correlation-id/self":
            assert json.loads(request.content) == {}
            correlation_requests.append(request)
            return httpx.Response(200, json={"correlation_id": "builder-correlation"})
        assert request.url.path == "/api/v1/llm/chat/completions"
        assert "X-Dograh-Usage-Context" not in request.headers
        body = json.loads(request.content)
        assert body["metadata"]["usage_context"] == "workflow_builder"
        assert body["metadata"]["correlation_id"] == "builder-correlation"
        assert body["metadata"]["mps_billing_version"] == "2"
        assert "org-service-key" not in str(body)
        system = next(
            message["content"]
            for message in body["messages"]
            if message["role"] == "system"
        )
        stage = next(
            stage
            for stage in ("plan", "build", "review")
            if builder_instructions(stage) == system
        )
        assert body["metadata"]["builder_stage"] == stage
        trace_headers.append(request.headers["traceparent"])
        assert (
            body["metadata"]["builder_session_id"]
            == f"workflow-builder:27:3:{session_id}"
        )
        assert body["metadata"]["builder_turn_id"]
        calls.append(body)
        tools = {tool["function"]["name"] for tool in body["tools"]}
        from api.services.workflow.builder_runtime.agent import (
            LOCAL_TOOLS,
            STAGE_MCP_TOOLS,
        )

        assert tools == STAGE_MCP_TOOLS[stage] | {
            tool.name for tool in LOCAL_TOOLS[stage]
        }
        name, arguments = replies.pop(0)
        message = {"role": "assistant", "content": arguments if name is None else None}
        if name:
            message["tool_calls"] = [
                {
                    "id": f"call-{len(calls)}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        return httpx.Response(200, json={"choices": [{"message": message}]})

    app = FastAPI()
    app.include_router(router)
    user = SimpleNamespace(id=3, selected_organization_id=27)
    app.dependency_overrides[get_user] = lambda: user
    # Keep the test browser's ASGI transport separate from outbound model calls.
    browser = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    actual_client = httpx.AsyncClient
    monkeypatch.setattr(
        mps_client_module.httpx,
        "AsyncClient",
        lambda **kwargs: actual_client(
            transport=httpx.MockTransport(complete), **kwargs
        ),
    )
    session_id = uuid4()

    async def turn(payload):
        response = await browser.post(
            f"/workflow/builder/{session_id}/turn",
            json={"request_id": str(uuid4()), **payload},
        )
        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "done", events
        return [event["session"] for event in events if event["type"] == "session"][-1]

    async with browser:
        first = await turn({"message": "Build a receptionist"})
        assert first["pending"][0]["kind"] == "questions"
        plan = await turn(
            {
                "checkpoint": first["checkpoint"],
                "answers": {first["pending"][0]["id"]: {"direction": ["Inbound"]}},
            }
        )
        assert plan["pending"][0]["kind"] == "plan_approval"
        assert plan["proposal"] is None
        assert len(calls) == 2  # Only discovery and the brief; no Builder calls.
        restored_plan = await browser.get(f"/workflow/builder/{session_id}")
        assert restored_plan.json()["pending"] == plan["pending"]
        pending = plan["pending"][0]
        final = await turn(
            {
                "checkpoint": plan["checkpoint"],
                "approval": {
                    "interrupt_id": pending["id"],
                    "brief_revision": pending["brief_revision"],
                    "decision": "approve",
                },
            }
        )
        assert not final["can_continue"]
        assert len(final["proposal"]["workflow"]["nodes"]) == 3
        restored = await browser.get(f"/workflow/builder/{session_id}")
        assert restored.json()["proposal"] == final["proposal"]
        assert "org-service-key" not in restored.text
        assert "org-service-key" not in str(
            await saver.aget_tuple(
                {"configurable": {"thread_id": f"workflow-builder:27:3:{session_id}"}}
            )
        )
        saved = await browser.post(
            f"/workflow/builder/{session_id}/save",
            json={"checkpoint": final["checkpoint"]},
        )
        assert saved.json() == {"workflow_id": 92}
        user.selected_organization_id = 28
        assert (await browser.get(f"/workflow/builder/{session_id}")).status_code == 404
    assert create.call_args.kwargs["organization_id"] == 27
    assert len(calls) == expected_calls
    assert len(correlation_requests) == expected_calls
    assert all(args.args[0] == 27 for args in config.await_args_list)
    # Real MCP stage results reached the model, not just successful tool requests.
    guide_results = [
        json.loads(message["content"])
        for call in calls
        for message in call["messages"][-1:]
        if message["role"] == "tool" and "stage" in json.loads(message["content"])
    ]
    assert [result["stage"] for result in guide_results] == [
        "create",
        "create",
        "create",
        "review",
        "review",
        "review",
    ]
    assert all(result["topics"] for result in guide_results)
    assert not replies
    assert builder_mcp_user.get() is None
    from api.services.workflow.builder_runtime.tracing import current_builder_trace

    assert current_builder_trace.get() is None
    spans = captured_traces.get_finished_spans()
    roots = [s for s in spans if s.name.startswith("workflow_builder.")]
    assert [s.attributes["builder.outcome"] for s in roots] == [
        "waiting_for_answers",
        "waiting_for_approval",
        "proposed",
        "saved",
    ]
    assert len({s.attributes["session.id"] for s in roots}) == 1
    assert len({s.context.trace_id for s in roots}) == 4
    turns = {format(s.context.trace_id, "032x"): s for s in roots[:3]}
    assert {header.split("-")[1] for header in trace_headers} == set(turns)
    for header in trace_headers:
        root = turns[header.split("-")[1]]
        assert header.split("-")[2] == format(root.context.span_id, "016x")
    for child in (s for s in spans if s.name.startswith("mcp.")):
        root = turns[format(child.context.trace_id, "032x")]
        assert child.parent.span_id == root.context.span_id
        assert child.attributes["session.id"] == root.attributes["session.id"]
        assert child.attributes["mcp.org_id"] == "27"
    assert json.loads(roots[-1].attributes["langfuse.observation.output"]) == {
        "workflow_id": 92
    }


@pytest.mark.asyncio
async def test_builder_stream_directs_unconfigured_org_to_model_configurations(
    monkeypatch,
):
    from langgraph.checkpoint.memory import InMemorySaver

    from api.enums import OrganizationConfigurationKey
    from api.routes.workflow_builder import router
    from api.services.auth.depends import get_user
    from api.services.workflow.builder_runtime import model as adapter
    from api.services.workflow.builder_runtime import service as runtime

    @asynccontextmanager
    async def checkpoints(thread_id):
        yield InMemorySaver()

    config = AsyncMock(return_value=None)
    minted = AsyncMock()
    monkeypatch.setattr(runtime.db_client, "builder_checkpointer", checkpoints)
    monkeypatch.setattr(runtime.db_client, "get_configuration", config)
    monkeypatch.setattr(adapter.mps_service_key_client, "create_correlation_id", minted)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=3, selected_organization_id=27
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as browser:
        response = await browser.post(
            f"/workflow/builder/{uuid4()}/turn",
            json={"request_id": str(uuid4()), "message": "Build a receptionist"},
        )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert events[-1] == {
        "type": "error",
        "message": "Agent builder is currently only supported with a Dograh Service Key. Set it in /model-configurations.",
    }
    config.assert_awaited_once_with(
        27, OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value
    )
    minted.assert_not_awaited()
