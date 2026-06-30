import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.processors.aggregators.llm_context import LLMSpecificMessage

from api.db.models import OrganizationModel, UserModel
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.workflow.text_chat_runner import (
    _deserialize_text_chat_checkpoint_messages,
    _serialize_text_chat_checkpoint_messages,
)
from api.tests.integrations._run_pipeline_helpers import USER_CONFIGURATION
from pipecat.tests import MockLLMService


def _log_texts(logs: dict | None, event_type: str) -> list[str]:
    events = (logs or {}).get("realtime_feedback_events") or []
    return [
        event.get("payload", {}).get("text", "")
        for event in events
        if event.get("type") == event_type
    ]


def test_text_chat_checkpoint_messages_round_trip_google_thought_signature():
    signature = bytes.fromhex("12340a32010c39d6c7f38fd8b8eb6ab0")
    messages = [
        {"role": "assistant", "content": "Hello."},
        {
            "role": "user",
            "content": "Hi",
        },
        LLMSpecificMessage(
            llm="google",
            message={
                "type": "thought_signature",
                "signature": signature,
                "bookmark": {"text": "Hello."},
            },
        ),
    ]

    encoded = _serialize_text_chat_checkpoint_messages(messages)

    json.dumps(encoded)
    assert encoded[-1] == {
        "__specific__": True,
        "llm": "google",
        "message": {
            "type": "thought_signature",
            "signature": {
                "__type__": "bytes",
                "__data__": "EjQKMgEMOdbH84/YuOtqsA==",
            },
            "bookmark": {"text": "Hello."},
        },
    }

    restored = _deserialize_text_chat_checkpoint_messages(encoded)

    assert restored[:2] == messages[:2]
    assert isinstance(restored[-1], LLMSpecificMessage)
    assert restored[-1].llm == "google"
    assert restored[-1].message["signature"] == signature
    assert restored[-1].message["bookmark"] == {"text": "Hello."}


async def _create_user_and_workflow(
    db_session,
    async_session,
    *,
    workflow_definition: dict,
    suffix: str,
):
    org = OrganizationModel(provider_id=f"textchat-org-{suffix}")
    async_session.add(org)
    await async_session.flush()

    user = UserModel(
        provider_id=f"textchat-user-{suffix}",
        selected_organization_id=org.id,
    )
    async_session.add(user)
    await async_session.flush()

    await db_session.update_user_configuration(
        user_id=user.id,
        configuration=EffectiveAIModelConfiguration.model_validate(USER_CONFIGURATION),
    )

    workflow = await db_session.create_workflow(
        name=f"Text Chat Workflow {suffix}",
        workflow_definition=workflow_definition,
        user_id=user.id,
        organization_id=org.id,
    )

    return user, workflow


@pytest.mark.asyncio
async def test_text_chat_session_creation_requires_selected_organization():
    from httpx import ASGITransport, AsyncClient

    from api.app import app
    from api.services.auth.depends import get_user

    user = UserModel(provider_id="textchat-user-no-selected-org")

    async def mock_get_user():
        return user

    original_override = app.dependency_overrides.get(get_user)
    app.dependency_overrides[get_user] = mock_get_user

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/workflow/123/text-chat/sessions", json={}
            )
    finally:
        if original_override:
            app.dependency_overrides[get_user] = original_override
        else:
            app.dependency_overrides.pop(get_user, None)

    assert response.status_code == 400
    assert response.json() == {"detail": "No organization selected"}


@pytest.mark.asyncio
async def test_text_chat_session_creation_executes_initial_assistant_turn(
    db_session,
    async_session,
    test_client_factory,
):
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are a helpful assistant.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "End",
                    "prompt": "Wrap up the conversation.",
                    "is_end": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
        ],
        "edges": [
            {
                "id": "start-end",
                "source": "start",
                "target": "end",
                "data": {"label": "End Call", "condition": "When the task is done."},
            }
        ],
    }

    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="bootstrap",
    )

    llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_text_chunks("Hello from the workflow tester.")
        ],
        chunk_delay=0.001,
    )

    async with test_client_factory(user) as client:
        with (
            patch(
                "api.services.workflow.text_chat_runner.create_llm_service",
                return_value=llm,
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            created = create_response.json()
            run_response = await client.get(
                f"/api/v1/workflow/{workflow.id}/runs/{created['workflow_run_id']}"
            )
            assert run_response.status_code == 200
            run_payload = run_response.json()

    turns = created["session_data"]["turns"]
    assert created["revision"] == 2
    assert created["session_data"]["status"] == "idle"
    assert len(turns) == 1
    assert turns[0]["status"] == "completed"
    assert turns[0]["user_message"] is None
    assert turns[0]["assistant_message"]["text"] == "Hello from the workflow tester."
    assert turns[0]["checkpoint_after_turn"]["current_node_id"] == "start"
    assert created["checkpoint"]["current_node_id"] == "start"
    assert created["state"] == "running"
    assert "Start" in (created["gathered_context"] or {}).get("nodes_visited", [])
    workflow_run = await db_session.get_workflow_run_by_id(created["workflow_run_id"])
    assert workflow_run is not None
    assert "call_duration_seconds" in workflow_run.usage_info
    assert _log_texts(run_payload["logs"], "rtf-bot-text") == [
        "Hello from the workflow tester."
    ]


@pytest.mark.asyncio
async def test_text_chat_message_executes_assistant_turn(
    db_session,
    async_session,
    test_client_factory,
):
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are a helpful assistant.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to the workflow tester.",
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "End",
                    "prompt": "Wrap up the conversation.",
                    "is_end": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
        ],
        "edges": [
            {
                "id": "start-end",
                "source": "start",
                "target": "end",
                "data": {"label": "End Call", "condition": "When the task is done."},
            }
        ],
    }

    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="basic",
    )

    llm_responses = [
        MockLLMService(mock_steps=[], chunk_delay=0.001),
        MockLLMService(
            mock_steps=[
                MockLLMService.create_text_chunks("Hello from the workflow tester.")
            ],
            chunk_delay=0.001,
        ),
    ]

    async with test_client_factory(user) as client:
        with (
            patch(
                "api.services.workflow.text_chat_runner.create_llm_service",
                side_effect=llm_responses,
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            created = create_response.json()

            message_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{created['workflow_run_id']}/messages",
                json={
                    "text": "Hi there",
                    "expected_revision": created["revision"],
                },
            )
            assert message_response.status_code == 200
            run_response = await client.get(
                f"/api/v1/workflow/{workflow.id}/runs/{created['workflow_run_id']}"
            )
            assert run_response.status_code == 200
            run_payload = run_response.json()

    payload = message_response.json()
    turns = payload["session_data"]["turns"]
    assert payload["revision"] == 4
    assert payload["session_data"]["status"] == "idle"
    assert len(turns) == 2
    assert turns[0]["user_message"] is None
    assert turns[0]["assistant_message"]["text"] == "Welcome to the workflow tester."
    assert turns[1]["status"] == "completed"
    assert turns[1]["user_message"]["text"] == "Hi there"
    assert turns[1]["assistant_message"]["text"] == "Hello from the workflow tester."
    assert turns[1]["checkpoint_after_turn"]["current_node_id"] == "start"
    assert payload["checkpoint"]["current_node_id"] == "start"
    assert payload["state"] == "running"
    assert "Start" in (payload["gathered_context"] or {}).get("nodes_visited", [])
    workflow_run = await db_session.get_workflow_run_by_id(created["workflow_run_id"])
    assert workflow_run is not None
    assert "call_duration_seconds" in workflow_run.usage_info
    assert _log_texts(run_payload["logs"], "rtf-user-transcription") == ["Hi there"]
    assert _log_texts(run_payload["logs"], "rtf-bot-text") == [
        "Welcome to the workflow tester.",
        "Hello from the workflow tester.",
    ]


@pytest.mark.asyncio
async def test_text_chat_executes_deferred_tool_calls_after_text_response(
    db_session,
    async_session,
    test_client_factory,
):
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are at the start node.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to the workflow tester.",
                },
            },
            {
                "id": "agent1",
                "type": "agentNode",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "Agent One",
                    "prompt": "You are in agent one.",
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
        ],
        "edges": [
            {
                "id": "start-agent1",
                "source": "start",
                "target": "agent1",
                "data": {
                    "label": "Go To Agent One",
                    "condition": "Move to agent one.",
                },
            }
        ],
    }

    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="mixed-tool-turn",
    )

    llm_responses = [
        MockLLMService(mock_steps=[], chunk_delay=0.001),
        MockLLMService(
            mock_steps=[
                MockLLMService.create_mixed_chunks(
                    "Let me transfer you.",
                    "go_to_agent_one",
                    {},
                    tool_call_id="call_agent_one",
                ),
                MockLLMService.create_text_chunks("Agent one here."),
            ],
            chunk_delay=0.001,
        ),
    ]

    async with test_client_factory(user) as client:
        with (
            patch(
                "api.services.workflow.text_chat_runner.create_llm_service",
                side_effect=llm_responses,
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            session = create_response.json()

            message_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{session['workflow_run_id']}/messages",
                json={
                    "text": "Please transfer me",
                    "expected_revision": session["revision"],
                },
            )
            assert message_response.status_code == 200
            run_response = await client.get(
                f"/api/v1/workflow/{workflow.id}/runs/{session['workflow_run_id']}"
            )
            assert run_response.status_code == 200

    payload = message_response.json()
    run_payload = run_response.json()
    assistant_text = payload["session_data"]["turns"][1]["assistant_message"]["text"]

    assert "Let me transfer you." in assistant_text
    assert "Agent one here." in assistant_text
    assert payload["checkpoint"]["current_node_id"] == "agent1"
    assert any(
        event["type"] == "tool_call_started"
        and event["payload"]["function_name"] == "go_to_agent_one"
        for event in payload["session_data"]["turns"][1]["events"]
    )
    node_transition_names = [
        event["payload"]["node_name"]
        for event in run_payload["logs"]["realtime_feedback_events"]
        if event["type"] == "rtf-node-transition"
    ]
    assert node_transition_names == ["Start", "Agent One"]
    function_call_event_names = [
        event["type"]
        for event in run_payload["logs"]["realtime_feedback_events"]
        if event["type"] in {"rtf-function-call-start", "rtf-function-call-end"}
    ]
    assert function_call_event_names == [
        "rtf-function-call-start",
        "rtf-function-call-end",
    ]


@pytest.mark.asyncio
async def test_text_chat_chains_multiple_follow_up_completions_in_one_turn(
    db_session,
    async_session,
    test_client_factory,
):
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are at the start node.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to the workflow tester.",
                },
            },
            {
                "id": "agent1",
                "type": "agentNode",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "Agent One",
                    "prompt": "You are in agent one.",
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
            {
                "id": "agent2",
                "type": "agentNode",
                "position": {"x": 0, "y": 400},
                "data": {
                    "name": "Agent Two",
                    "prompt": "You are in agent two.",
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
        ],
        "edges": [
            {
                "id": "start-agent1",
                "source": "start",
                "target": "agent1",
                "data": {
                    "label": "Go To Agent One",
                    "condition": "Move to agent one.",
                },
            },
            {
                "id": "agent1-agent2",
                "source": "agent1",
                "target": "agent2",
                "data": {
                    "label": "Go To Agent Two",
                    "condition": "Move to agent two.",
                },
            },
        ],
    }

    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="multi-hop-turn",
    )

    llm_responses = [
        MockLLMService(mock_steps=[], chunk_delay=0.001),
        MockLLMService(
            mock_steps=[
                MockLLMService.create_mixed_chunks(
                    "Moving to agent one.",
                    "go_to_agent_one",
                    {},
                    tool_call_id="call_agent_one",
                ),
                MockLLMService.create_mixed_chunks(
                    "Moving to agent two.",
                    "go_to_agent_two",
                    {},
                    tool_call_id="call_agent_two",
                ),
                MockLLMService.create_text_chunks("Agent two here."),
            ],
            chunk_delay=0.001,
        ),
    ]

    async with test_client_factory(user) as client:
        with (
            patch(
                "api.services.workflow.text_chat_runner.create_llm_service",
                side_effect=llm_responses,
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            session = create_response.json()

            message_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{session['workflow_run_id']}/messages",
                json={
                    "text": "Please route me through the flow",
                    "expected_revision": session["revision"],
                },
            )
            assert message_response.status_code == 200

    payload = message_response.json()
    assistant_text = payload["session_data"]["turns"][1]["assistant_message"]["text"]

    assert "Moving to agent one." in assistant_text
    assert "Moving to agent two." in assistant_text
    assert "Agent two here." in assistant_text
    assert payload["checkpoint"]["current_node_id"] == "agent2"
    assert (
        sum(
            1
            for event in payload["session_data"]["turns"][1]["events"]
            if event["type"] == "tool_call_started"
        )
        == 2
    )


@pytest.mark.asyncio
async def test_text_chat_greeting_only_plays_on_fresh_node_entry(
    db_session,
    async_session,
    test_client_factory,
):
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are a helpful assistant.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to the workflow tester.",
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "End",
                    "prompt": "Wrap up the conversation.",
                    "is_end": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
        ],
        "edges": [
            {
                "id": "start-end",
                "source": "start",
                "target": "end",
                "data": {"label": "End Call", "condition": "When the task is done."},
            }
        ],
    }

    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="greeting-once",
    )

    llm_responses = [
        MockLLMService(mock_steps=[], chunk_delay=0.001),
        MockLLMService(
            mock_steps=[MockLLMService.create_text_chunks("First answer.")],
            chunk_delay=0.001,
        ),
        MockLLMService(
            mock_steps=[MockLLMService.create_text_chunks("Second answer.")],
            chunk_delay=0.001,
        ),
    ]

    async with test_client_factory(user) as client:
        with (
            patch(
                "api.services.workflow.text_chat_runner.create_llm_service",
                side_effect=llm_responses,
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            session = create_response.json()
            opening_text = session["session_data"]["turns"][0]["assistant_message"][
                "text"
            ]

            first_message = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{session['workflow_run_id']}/messages",
                json={
                    "text": "First turn",
                    "expected_revision": session["revision"],
                },
            )
            assert first_message.status_code == 200
            first_payload = first_message.json()

            second_message = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{session['workflow_run_id']}/messages",
                json={
                    "text": "Second turn",
                    "expected_revision": first_payload["revision"],
                },
            )
            assert second_message.status_code == 200

    first_text = first_payload["session_data"]["turns"][1]["assistant_message"]["text"]
    second_text = second_message.json()["session_data"]["turns"][2][
        "assistant_message"
    ]["text"]

    assert opening_text == "Welcome to the workflow tester."
    assert "Welcome to the workflow tester." not in first_text
    assert "First answer." in first_text
    assert "Welcome to the workflow tester." not in second_text
    assert "Second answer." in second_text


@pytest.mark.asyncio
async def test_text_chat_rewind_reuses_checkpoint_snapshot(
    db_session,
    async_session,
    test_client_factory,
):
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are at the start node.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to the rewind test.",
                },
            },
            {
                "id": "agent1",
                "type": "agentNode",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "Agent One",
                    "prompt": "You are in agent one.",
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
            {
                "id": "agent2",
                "type": "agentNode",
                "position": {"x": 0, "y": 400},
                "data": {
                    "name": "Agent Two",
                    "prompt": "You are in agent two.",
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 0, "y": 600},
                "data": {
                    "name": "End",
                    "prompt": "You are at the end node.",
                    "is_end": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
        ],
        "edges": [
            {
                "id": "start-agent1",
                "source": "start",
                "target": "agent1",
                "data": {
                    "label": "Go To Agent One",
                    "condition": "Move to agent one.",
                },
            },
            {
                "id": "agent1-agent2",
                "source": "agent1",
                "target": "agent2",
                "data": {
                    "label": "Go To Agent Two",
                    "condition": "Move to agent two.",
                },
            },
            {
                "id": "agent2-end",
                "source": "agent2",
                "target": "end",
                "data": {"label": "Finish", "condition": "End the flow."},
            },
        ],
    }

    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="rewind",
    )

    llm_responses = [
        MockLLMService(mock_steps=[], chunk_delay=0.001),
        MockLLMService(
            mock_steps=[
                MockLLMService.create_function_call_chunks(
                    "go_to_agent_one",
                    {},
                    tool_call_id="call_agent_one",
                ),
                MockLLMService.create_text_chunks("Agent one here."),
            ],
            chunk_delay=0.001,
        ),
        MockLLMService(
            mock_steps=[
                MockLLMService.create_function_call_chunks(
                    "go_to_agent_two",
                    {},
                    tool_call_id="call_agent_two",
                ),
                MockLLMService.create_text_chunks("Agent two here."),
            ],
            chunk_delay=0.001,
        ),
        MockLLMService(
            mock_steps=[MockLLMService.create_text_chunks("Back in agent one.")],
            chunk_delay=0.001,
        ),
    ]

    async with test_client_factory(user) as client:
        with (
            patch(
                "api.services.workflow.text_chat_runner.create_llm_service",
                side_effect=llm_responses,
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            session = create_response.json()

            first_message = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{session['workflow_run_id']}/messages",
                json={
                    "text": "First turn",
                    "expected_revision": session["revision"],
                },
            )
            assert first_message.status_code == 200
            first_payload = first_message.json()
            first_turn_id = first_payload["session_data"]["turns"][1]["id"]
            assert first_payload["checkpoint"]["current_node_id"] == "agent1"

            second_message = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{session['workflow_run_id']}/messages",
                json={
                    "text": "Second turn",
                    "expected_revision": first_payload["revision"],
                },
            )
            assert second_message.status_code == 200
            second_payload = second_message.json()
            assert second_payload["checkpoint"]["current_node_id"] == "agent2"

            rewind_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{session['workflow_run_id']}/rewind",
                json={
                    "cursor_turn_id": first_turn_id,
                    "expected_revision": second_payload["revision"],
                },
            )
            assert rewind_response.status_code == 200
            rewound = rewind_response.json()
            assert rewound["session_data"]["cursor_turn_id"] == first_turn_id
            rewound_run_response = await client.get(
                f"/api/v1/workflow/{workflow.id}/runs/{session['workflow_run_id']}"
            )
            assert rewound_run_response.status_code == 200
            rewound_run_payload = rewound_run_response.json()

            third_message = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{session['workflow_run_id']}/messages",
                json={
                    "text": "Third turn after rewind",
                    "expected_revision": rewound["revision"],
                },
            )
            assert third_message.status_code == 200
            final_run_response = await client.get(
                f"/api/v1/workflow/{workflow.id}/runs/{session['workflow_run_id']}"
            )
            assert final_run_response.status_code == 200
            final_run_payload = final_run_response.json()

    payload = third_message.json()
    assert payload["checkpoint"]["current_node_id"] == "agent1"
    assert payload["session_data"]["discarded_future"]
    assert len(payload["session_data"]["turns"]) == 3
    assert payload["session_data"]["turns"][1]["id"] == first_turn_id
    assert (
        payload["session_data"]["turns"][2]["assistant_message"]["text"]
        == "Back in agent one."
    )
    assert _log_texts(rewound_run_payload["logs"], "rtf-user-transcription") == [
        "First turn"
    ]
    assert "Second turn" not in _log_texts(
        rewound_run_payload["logs"], "rtf-user-transcription"
    )
    assert "Agent two here." not in _log_texts(
        rewound_run_payload["logs"], "rtf-bot-text"
    )
    assert _log_texts(final_run_payload["logs"], "rtf-user-transcription") == [
        "First turn",
        "Third turn after rewind",
    ]
    assert _log_texts(final_run_payload["logs"], "rtf-bot-text") == [
        "Welcome to the rewind test.",
        "Agent one here.",
        "Back in agent one.",
    ]


@pytest.mark.asyncio
async def test_text_chat_session_is_not_accessible_from_another_org(
    db_session,
    async_session,
    test_client_factory,
):
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are a helpful assistant.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "End",
                    "prompt": "Wrap up the conversation.",
                    "is_end": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            },
        ],
        "edges": [
            {
                "id": "start-end",
                "source": "start",
                "target": "end",
                "data": {"label": "End Call", "condition": "When the task is done."},
            }
        ],
    }

    owner_user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="owner",
    )
    other_user, _ = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="other",
    )

    async with test_client_factory(owner_user) as owner_client:
        llm = MockLLMService(
            mock_steps=[
                MockLLMService.create_text_chunks("Hello from the workflow tester.")
            ],
            chunk_delay=0.001,
        )
        with (
            patch(
                "api.services.workflow.text_chat_runner.create_llm_service",
                return_value=llm,
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            create_response = await owner_client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            created = create_response.json()

    async with test_client_factory(other_user) as other_client:
        get_response = await other_client.get(
            f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{created['workflow_run_id']}"
        )
        assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_text_chat_session_creation_requires_selected_org_scope(
    db_session,
    async_session,
    test_client_factory,
):
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are a helpful assistant.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            }
        ],
        "edges": [],
    }

    org_a = OrganizationModel(provider_id="textchat-scope-a")
    org_b = OrganizationModel(provider_id="textchat-scope-b")
    async_session.add_all([org_a, org_b])
    await async_session.flush()

    user = UserModel(
        provider_id="textchat-scope-user",
        selected_organization_id=org_a.id,
    )
    async_session.add(user)
    await async_session.flush()

    await db_session.update_user_configuration(
        user_id=user.id,
        configuration=EffectiveAIModelConfiguration.model_validate(USER_CONFIGURATION),
    )

    workflow = await db_session.create_workflow(
        name="Cross-org workflow",
        workflow_definition=workflow_definition,
        user_id=user.id,
        organization_id=org_b.id,
    )

    llm = MockLLMService(
        mock_steps=[MockLLMService.create_text_chunks("Should never run.")],
        chunk_delay=0.001,
    )

    async with test_client_factory(user) as client:
        with (
            patch(
                "api.services.workflow.text_chat_runner.create_llm_service",
                return_value=llm,
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )

    assert create_response.status_code == 404
    _, total_count = await db_session.get_workflow_runs_by_workflow_id(
        workflow.id,
        organization_id=org_b.id,
    )
    assert total_count == 0


@pytest.mark.asyncio
async def test_text_chat_session_creation_rejects_quota_before_creating_run(
    db_session,
    async_session,
    test_client_factory,
):
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are a helpful assistant.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            }
        ],
        "edges": [],
    }

    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="quota-create",
    )

    async with test_client_factory(user) as client:
        with patch(
            "api.routes.workflow_text_chat.authorize_workflow_run_start",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    has_quota=False,
                    error_message="Quota exceeded",
                )
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )

    assert create_response.status_code == 402
    assert create_response.json()["detail"] == "Quota exceeded"
    runs, total_count = await db_session.get_workflow_runs_by_workflow_id(
        workflow.id,
        organization_id=workflow.organization_id,
    )
    assert total_count == 1
    text_session = await db_session.get_workflow_run_text_session(
        runs[0].id,
        organization_id=workflow.organization_id,
    )
    assert text_session is None


@pytest.mark.asyncio
async def test_text_chat_append_rejects_quota_without_mutating_session(
    db_session,
    async_session,
    test_client_factory,
):
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "You are a helpful assistant.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                },
            }
        ],
        "edges": [],
    }

    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="quota-append",
    )

    llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_text_chunks("Hello from the workflow tester.")
        ],
        chunk_delay=0.001,
    )

    async with test_client_factory(user) as client:
        with (
            patch(
                "api.routes.workflow_text_chat.authorize_workflow_run_start",
                new=AsyncMock(
                    side_effect=[
                        SimpleNamespace(has_quota=True, error_message=""),
                        SimpleNamespace(
                            has_quota=False,
                            error_message="Quota exceeded on append",
                        ),
                    ]
                ),
            ),
            patch(
                "api.services.workflow.text_chat_runner.create_llm_service",
                return_value=llm,
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            created = create_response.json()

            append_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{created['workflow_run_id']}/messages",
                json={
                    "text": "This should be rejected",
                    "expected_revision": created["revision"],
                },
            )
            assert append_response.status_code == 402

            session_response = await client.get(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{created['workflow_run_id']}"
            )
            assert session_response.status_code == 200

    session_payload = session_response.json()
    assert append_response.json()["detail"] == "Quota exceeded on append"
    assert session_payload["revision"] == created["revision"]
    assert session_payload["session_data"]["turns"] == created["session_data"]["turns"]
    assert (
        session_payload["session_data"]["status"] == created["session_data"]["status"]
    )
