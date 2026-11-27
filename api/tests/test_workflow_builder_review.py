"""Regression checks for resource validation and recoverable builder inputs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from api.schemas.workflow_builder import BuilderTurnRequest
from api.services.observability.trace_payloads import trace_json
from api.services.workflow import builder_validation as validation
from api.services.workflow.authoring.contracts import CallBrief
from api.services.workflow.builder_runtime.schemas import BuilderQuestion
from api.tests.support.workflow_authoring import BRIEF
from api.tests.test_workflow_builder import VALID_CODE


@pytest.mark.asyncio
async def test_preview_resolves_older_documents_by_uuid_with_org_scope(monkeypatch):
    lookup = AsyncMock(return_value=SimpleNamespace(document_uuid="older-document"))
    monkeypatch.setattr(validation.db_client, "get_document_by_uuid", lookup)
    code = VALID_CODE.replace(
        'name: "Qualify",', 'name: "Qualify", document_uuids: ["older-document"],'
    )
    assert (await validation.preview_workflow_code(code, 17))["valid"]
    lookup.assert_awaited_once_with("older-document", organization_id=17)
    lookup.return_value = None
    assert not (await validation.preview_workflow_code(code, 17))["valid"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identifier,valid", [("42", True), ("welcome-message", False), ("43", False)]
)
async def test_greeting_and_edge_audio_use_recording_primary_keys(
    monkeypatch, identifier, valid
):
    catalog = AsyncMock(
        return_value=[SimpleNamespace(id=42, recording_id="welcome-message")]
    )
    monkeypatch.setattr(validation.db_client, "get_recordings", catalog)
    code = VALID_CODE.replace(
        'name: "Welcome",',
        f'name: "Welcome", greeting_type: "audio", greeting_recording_id: "{identifier}",',
    )
    code = code.replace(
        'label: "Continue",',
        f'label: "Continue", transition_speech_type: "audio", transition_speech_recording_id: "{identifier}",',
    )
    assert (await validation.preview_workflow_code(code, 17))["valid"] is valid
    catalog.assert_awaited_once_with(organization_id=17)


def test_legacy_briefs_preserve_removed_business_details_and_reject_unknown_fields():
    old = {
        **BRIEF,
        "audience": "Existing customers",
        "trigger": "New reservation",
        "information_available_at_start": ["Name"],
        "conversation_steps": ["Confirm booking"],
        "data_operations": [{"purpose": "Look up booking"}],
        "exit_conditions": ["Caller refuses"],
        "acceptance_scenarios": ["Booking confirmed"],
        "open_questions": [],
    }
    brief = CallBrief.model_validate(old)
    for text in (
        "Existing customers",
        "New reservation",
        "Name",
        "Confirm booking",
        "Look up booking",
        "Caller refuses",
        "Booking confirmed",
    ):
        assert text in " ".join(brief.business_rules)
    with pytest.raises(ValidationError):
        CallBrief.model_validate({**BRIEF, "invented_field": "invalid"})


@pytest.mark.parametrize("options", [[" "], ["Yes", " Yes "]])
def test_questions_reject_blank_or_duplicate_normalized_options(options):
    with pytest.raises(ValidationError):
        BuilderQuestion(
            id="choice", title="Choose", options=options, allow_custom=False
        )


def test_blank_messages_are_rejected_but_explicit_resume_requests_remain_supported():
    with pytest.raises(ValidationError):
        BuilderTurnRequest(request_id=uuid4(), message=" \n ")
    assert BuilderTurnRequest(request_id=uuid4(), checkpoint="saved").message is None


def test_trace_payload_accepts_lone_surrogates():
    import json

    assert json.loads(trace_json({"message": "\ud800"})) == {"message": "\ud800"}


@pytest.mark.asyncio
async def test_tool_catalog_survives_resume_without_resending_specs(monkeypatch):
    from contextlib import asynccontextmanager

    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from api.services.workflow.builder_runtime import service
    from api.services.workflow.builder_runtime.schemas import BuilderStep
    from api.tests.test_workflow_builder_runtime import ScriptedModel, tool_call

    saver = InMemorySaver()
    model = ScriptedModel(
        organization_id=17,
        user_id="3",
        replies=[
            tool_call("list_tools", {}, "catalog"),
            AIMessage(content="Here are your tools."),
        ],
    )

    @asynccontextmanager
    async def checkpointer(*args, **kwargs):
        yield saver

    monkeypatch.setattr(service.db_client, "builder_checkpointer", checkpointer)
    monkeypatch.setattr(service, "BuilderChatModel", lambda **kwargs: model)
    session = uuid4()
    first = await service.builder_step(
        session,
        17,
        "3",
        BuilderStep(
            request_id=uuid4(),
            message="Show my tools",
            current_workflow={"id": 1, "revision": "old", "code": VALID_CODE},
            tools=[
                {
                    "name": "list_tools",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ],
        ),
    )
    assert first["pending"][0]["tool_name"] == "list_tools"
    finished = await service.builder_step(
        session,
        17,
        "3",
        BuilderStep(
            request_id=uuid4(),
            checkpoint=first["checkpoint"],
            resume={first["pending"][0]["id"]: {"tools": []}},
        ),
    )
    assert not finished["pending"]
    assert not finished["can_continue"]
    assert any(
        message.type == "tool" and '"tools": []' in message.content
        for message in model.seen[-1]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["list_tools", "list_node_types"])
async def test_legacy_first_draft_catalog_interrupt_remains_executable(name):
    from langgraph.checkpoint.memory import InMemorySaver

    from api.services.workflow.builder_runtime.agent import build_agent
    from api.tests.test_workflow_builder_runtime import ScriptedModel, tool_call

    model = ScriptedModel(organization_id=17, user_id="3", replies=[])
    graph = build_agent(
        model,
        InMemorySaver(),
        [{"name": name, "inputSchema": {"type": "object", "properties": {}}}],
    )
    config = {"configurable": {"thread_id": str(uuid4())}}
    await graph.aupdate_state(
        config,
        {
            "phase": "build",
            "build_messages": [tool_call(name, {}, "old-read")],
            "brief": BRIEF,
            "brief_revision": 1,
            "approved_brief_revision": 1,
        },
        as_node="build",
    )
    await graph.ainvoke(None, config)
    snapshot = await graph.aget_state(config)
    pending = [
        interrupt.value for task in snapshot.tasks for interrupt in task.interrupts
    ]
    assert pending == [{"kind": "mcp", "tool_name": name, "arguments": {}}]
    assert not model.seen
