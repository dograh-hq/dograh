from unittest.mock import AsyncMock, patch

import pytest
from pipecat.tests import MockLLMService

from api.db.models import OrganizationModel, UserModel
from api.schemas.user_configuration import UserConfiguration
from api.tests.integrations._run_pipeline_helpers import USER_CONFIGURATION


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
        configuration=UserConfiguration.model_validate(USER_CONFIGURATION),
    )

    workflow = await db_session.create_workflow(
        name=f"Text Chat Workflow {suffix}",
        workflow_definition=workflow_definition,
        user_id=user.id,
        organization_id=org.id,
    )

    return user, workflow


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
        mock_steps=[MockLLMService.create_text_chunks("Hello from the workflow tester.")],
        chunk_delay=0.001,
    )

    async with test_client_factory(user) as client:
        with patch(
            "api.services.workflow.text_chat_runner.create_llm_service",
            return_value=llm,
        ), patch(
            "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
            new=AsyncMock(return_value=False),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            created = create_response.json()

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
            mock_steps=[MockLLMService.create_text_chunks("Hello from the workflow tester.")],
            chunk_delay=0.001,
        ),
    ]

    async with test_client_factory(user) as client:
        with patch(
            "api.services.workflow.text_chat_runner.create_llm_service",
            side_effect=llm_responses,
        ), patch(
            "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
            new=AsyncMock(return_value=False),
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
        with patch(
            "api.services.workflow.text_chat_runner.create_llm_service",
            side_effect=llm_responses,
        ), patch(
            "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
            new=AsyncMock(return_value=False),
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

    payload = message_response.json()
    assistant_text = payload["session_data"]["turns"][1]["assistant_message"]["text"]

    assert "Let me transfer you." in assistant_text
    assert "Agent one here." in assistant_text
    assert payload["checkpoint"]["current_node_id"] == "agent1"
    assert any(
        event["type"] == "tool_call_started"
        and event["payload"]["function_name"] == "go_to_agent_one"
        for event in payload["session_data"]["turns"][1]["events"]
    )


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
        with patch(
            "api.services.workflow.text_chat_runner.create_llm_service",
            side_effect=llm_responses,
        ), patch(
            "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
            new=AsyncMock(return_value=False),
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
    assert sum(
        1
        for event in payload["session_data"]["turns"][1]["events"]
        if event["type"] == "tool_call_started"
    ) == 2


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
        with patch(
            "api.services.workflow.text_chat_runner.create_llm_service",
            side_effect=llm_responses,
        ), patch(
            "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
            new=AsyncMock(return_value=False),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            session = create_response.json()
            opening_text = session["session_data"]["turns"][0]["assistant_message"]["text"]

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
    second_text = second_message.json()["session_data"]["turns"][2]["assistant_message"][
        "text"
    ]

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
        with patch(
            "api.services.workflow.text_chat_runner.create_llm_service",
            side_effect=llm_responses,
        ), patch(
            "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
            new=AsyncMock(return_value=False),
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

            third_message = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{session['workflow_run_id']}/messages",
                json={
                    "text": "Third turn after rewind",
                    "expected_revision": rewound["revision"],
                },
            )
            assert third_message.status_code == 200

    payload = third_message.json()
    assert payload["checkpoint"]["current_node_id"] == "agent1"
    assert payload["session_data"]["discarded_future"]
    assert len(payload["session_data"]["turns"]) == 3
    assert payload["session_data"]["turns"][1]["id"] == first_turn_id
    assert (
        payload["session_data"]["turns"][2]["assistant_message"]["text"]
        == "Back in agent one."
    )


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
            mock_steps=[MockLLMService.create_text_chunks("Hello from the workflow tester.")],
            chunk_delay=0.001,
        )
        with patch(
            "api.services.workflow.text_chat_runner.create_llm_service",
            return_value=llm,
        ), patch(
            "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
            new=AsyncMock(return_value=False),
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
