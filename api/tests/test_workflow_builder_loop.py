"""The v1 loop validates mutations and completes with a normal model answer."""

import asyncio
import json
from contextlib import aclosing

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from api.services.workflow.builder_runtime.agent import local_tools
from api.services.workflow.builder_runtime.progress import report_progress
from api.services.workflow.builder_runtime.tool_schemas import tool_request_schema
from api.services.workflow.builder_runtime.working_copy import (
    SourceEdit,
    patch_source,
    source_revision,
)
from api.tests.test_workflow_builder import VALID_CODE
from api.tests.test_workflow_builder_runtime import tool_call
from api.tests.test_workflow_builder_stages import Conversation, brief_call


def patch_call(code=VALID_CODE, edits=None, call_id="patch", **kwargs):
    return tool_call(
        "patch_workflow",
        {
            "expected_revision": source_revision(code),
            "edits": edits
            or [
                {
                    "old_text": "Greet the caller.",
                    "new_text": "Welcome the caller warmly.",
                }
            ],
            **kwargs,
        },
        call_id,
    )


async def open_editor(conversation):
    return await conversation.step(
        message="Update the greeting",
        current_workflow={"id": 41, "revision": "saved", "code": VALID_CODE},
    )


def test_patch_is_atomic_and_matches_against_original_revision():
    code = "first second third"
    revision = source_revision(code)
    edits = [
        SourceEdit(old_text="first", new_text="second"),
        SourceEdit(old_text="second", new_text="last"),
    ]
    assert patch_source(code, revision, edits)["code"] == "second last third"
    for edits in [
        [
            SourceEdit(old_text="first", new_text="updated"),
            SourceEdit(old_text="missing", new_text="new"),
        ],
        [
            SourceEdit(old_text="first second", new_text="updated"),
            SourceEdit(old_text="second", new_text="new"),
        ],
    ]:
        result = patch_source(code, revision, edits)
        assert "error" in result and "code" not in result
        assert result["revision"] == revision


@pytest.mark.asyncio
async def test_creation_writes_validates_and_answers_without_finish_tool():
    conversation = Conversation(
        [
            brief_call(),
            tool_call(
                "write_workflow",
                {
                    "code": VALID_CODE,
                    "expected_revision": source_revision(""),
                    "configuration_gaps": ["Connect the booking API."],
                },
                "write",
            ),
            AIMessage(content="Your receptionist is ready to try."),
        ]
    )
    await conversation.step(message="Build a receptionist")
    current = await conversation.approve()
    assert current["pending"][0]["tool_name"] == "preview_workflow"
    final = await conversation.validate(VALID_CODE)
    assert final["proposal"]["code"] == VALID_CODE
    assert final["proposal"]["configuration_gaps"] == ["Connect the booking API."]
    assert final["messages"][-1]["content"].startswith("Your receptionist is ready")
    assert not final["can_continue"]
    for iteration in (False, True):
        names = {tool.name for tool in local_tools("build", iteration=iteration)}
        assert {"write_workflow", "patch_workflow", "ask_questions"} <= names
        assert not names & {
            "validate_workflow",
            "finish_workflow",
            "propose_workflow",
            "test_workflow_scenario",
            "remember_decisions",
            "undo_workflow_edit",
        }


@pytest.mark.asyncio
async def test_patch_validates_once_returns_compact_result_and_preserves_prompt_prefix():
    edits = [
        {"old_text": "Greet the caller.", "new_text": "Welcome the caller warmly."},
        {"old_text": "Say goodbye.", "new_text": "Thank the caller and say goodbye."},
    ]
    conversation = Conversation(
        [
            patch_call(edits=edits),
            AIMessage(content="Updated the greeting and goodbye."),
        ]
    )
    await open_editor(conversation)
    pending = conversation.current["pending"][0]
    code = pending["arguments"]["code"]
    assert "Welcome the caller warmly." in code and "Thank the caller" in code
    assert conversation.current["proposal"] is None
    final = await conversation.validate(code)
    assert final["proposal"]["code"] == code
    assert len(conversation.model.seen) == 2
    first, second = conversation.model.seen
    assert second[: len(first)] == first
    result = json.loads(next(m.content for m in second if isinstance(m, ToolMessage)))
    assert result["valid"] and result["edits_applied"] == 2
    assert "diff" not in result
    stored = (await conversation.state())["tool_results"]
    assert "diff" in json.loads(next(iter(stored.values()))["content"])


@pytest.mark.asyncio
async def test_mixed_patch_and_read_execute_in_order_across_checkpoint_resume():
    conversation = Conversation(
        [
            AIMessage(
                content="",
                tool_calls=[
                    *patch_call().tool_calls,
                    *tool_call("read_workflow", {}, "read").tool_calls,
                ],
            ),
            AIMessage(content="Updated the greeting."),
        ]
    )
    await open_editor(conversation)
    edited = conversation.current["pending"][0]["arguments"]["code"]
    final = await conversation.validate(edited)
    results = [
        json.loads(m.content)
        for m in conversation.model.seen[-1]
        if isinstance(m, ToolMessage)
    ]
    assert len(results) == 2
    assert results[0]["revision"] == results[1]["revision"] == source_revision(edited)
    assert "Welcome the caller warmly." in results[1]["content"]
    assert final["proposal"]["code"] == edited
    assert not (await conversation.state())["pending_tool_calls"]


@pytest.mark.asyncio
async def test_bad_patch_arguments_return_a_tool_error_and_allow_repair():
    invalid = patch_call()
    invalid.tool_calls[0]["args"]["edits"] = []
    conversation = Conversation(
        [invalid, patch_call(call_id="repair"), AIMessage(content="Updated.")]
    )
    current = await open_editor(conversation)
    assert current["pending"][0]["tool_name"] == "preview_workflow"
    error = next(m for m in conversation.model.seen[-1] if isinstance(m, ToolMessage))
    assert error.status == "error"
    final = await conversation.validate(current["pending"][0]["arguments"]["code"])
    assert final["proposal"]


@pytest.mark.asyncio
async def test_invalid_draft_cannot_be_completed_by_a_success_claim():
    conversation = Conversation(
        [patch_call(), AIMessage(content="All done!"), AIMessage(content="It worked!")]
    )
    await open_editor(conversation)
    final = await conversation.resume({"valid": False, "errors": ["Broken transition"]})
    assert final["proposal"] is None and not final["can_continue"]
    assert "All done!" not in str(final["messages"])
    assert "Welcome the caller warmly." in (await conversation.state())["working_code"]


@pytest.mark.asyncio
async def test_invalid_patch_can_be_repaired_without_losing_the_working_copy():
    edited = VALID_CODE.replace("Greet the caller.", "Welcome the caller warmly.")
    conversation = Conversation(
        [
            patch_call(),
            patch_call(
                edited,
                [
                    {
                        "old_text": "Welcome the caller warmly.",
                        "new_text": "Welcome the caller.",
                    }
                ],
                call_id="repair",
            ),
            AIMessage(content="Updated the greeting."),
        ]
    )
    await open_editor(conversation)
    current = await conversation.resume(
        {"valid": False, "error": "Please repair the greeting."}
    )
    assert (await conversation.state())["working_code"] == edited
    fixed = current["pending"][0]["arguments"]["code"]
    final = await conversation.validate(fixed)
    assert final["proposal"]["code"] == fixed
    assert (await conversation.state())["validation_attempts"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [None, []])
async def test_edit_keeps_gaps_unless_explicitly_updated(remaining):
    conversation = Conversation(
        [
            patch_call(configuration_gaps=["Missing connection"]),
            AIMessage(content="Updated."),
            patch_call(
                VALID_CODE.replace("Greet the caller.", "Welcome the caller warmly."),
                [{"old_text": "Say goodbye.", "new_text": "Goodbye."}],
                **({"configuration_gaps": remaining} if remaining is not None else {}),
            ),
            AIMessage(content="Updated again."),
        ]
    )
    await open_editor(conversation)
    first = await conversation.validate(
        VALID_CODE.replace("Greet the caller.", "Welcome the caller warmly.")
    )
    assert first["proposal"]["configuration_gaps"] == ["Missing connection"]
    await conversation.step(message="Fix the goodbye and resolve the connection")
    second = await conversation.validate(
        conversation.current["pending"][0]["arguments"]["code"]
    )
    assert second["proposal"]["configuration_gaps"] == (
        remaining if remaining is not None else ["Missing connection"]
    )


def test_transfer_schema_is_complete_for_selected_mode_and_fits_inline():
    from jsonschema import Draft202012Validator

    from api.schemas.tool import CreateToolRequest

    static = tool_request_schema("transfer_call")["schema"]
    assert len(json.dumps(static)) < 8000
    assert "HttpTransferResolverConfig" not in static["$defs"]
    assert "ContextDestinationMappingConfig" not in static["$defs"]
    request = {
        "name": "Support",
        "definition": {
            "type": "transfer_call",
            "config": {"destination": "+12025550123"},
        },
    }
    Draft202012Validator(static).validate(request)
    assert (
        CreateToolRequest.model_validate(request).definition.config.introduction_enabled
        is False
    )
    dynamic = tool_request_schema("transfer_call", destination_source="dynamic")[
        "schema"
    ]
    assert "HttpTransferResolverConfig" in dynamic["$defs"]
    mapped = tool_request_schema("transfer_call", destination_source="context_mapping")[
        "schema"
    ]
    assert "ContextDestinationMappingConfig" in mapped["$defs"]


@pytest.mark.asyncio
async def test_local_progress_arrives_before_step_finishes_and_cancellation_cleans_up(
    monkeypatch,
):
    from api.services.workflow import builder

    release = asyncio.Event()
    stopped = asyncio.Event()

    async def blocked_step(*args):
        try:
            await asyncio.to_thread(report_progress, "Updating your agent…")
            await release.wait()
        finally:
            stopped.set()

    monkeypatch.setattr(builder, "local_step", blocked_step)
    async with aclosing(builder._local_step_events(None, None, {})) as events:
        event = await asyncio.wait_for(anext(events), 1)
        assert event == {"type": "status", "message": "Updating your agent…"}
        assert not stopped.is_set()
    assert stopped.is_set()
