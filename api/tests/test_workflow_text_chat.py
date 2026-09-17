import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.processors.aggregators.llm_context import LLMSpecificMessage
from pipecat.utils.enums import EndTaskReason

from api.db.models import OrganizationModel, UserModel, organization_users_association
from api.enums import OrganizationConfigurationKey
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.ai_model_configuration import (
    convert_legacy_ai_model_configuration_to_v2,
)
from api.services.workflow.text_chat_runner import (
    _deserialize_text_chat_checkpoint_messages,
    _serialize_text_chat_checkpoint_messages,
    extract_text_chat_final_variables,
)
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.services.workflow.text_chat_session_service import (
    TextChatSessionRevisionConflictError,
    append_text_chat_user_message,
    complete_text_chat_session,
)
from api.tasks.function_names import FunctionNames
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
    await async_session.execute(
        organization_users_association.insert().values(
            user_id=user.id,
            organization_id=org.id,
        )
    )

    user_configuration = EffectiveAIModelConfiguration.model_validate(
        USER_CONFIGURATION
    )
    await db_session.upsert_configuration(
        org.id,
        OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
        convert_legacy_ai_model_configuration_to_v2(user_configuration).model_dump(
            mode="json",
            exclude_none=True,
        ),
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
async def test_user_can_end_text_chat_session(
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
                },
            }
        ],
        "edges": [],
    }
    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="user-end",
    )
    workflow_run = await db_session.create_workflow_run(
        name="User-ended text chat",
        workflow_id=workflow.id,
        mode="textchat",
        user_id=user.id,
        organization_id=user.selected_organization_id,
    )
    text_session = await db_session.ensure_workflow_run_text_session(
        workflow_run.id,
        session_data={
            "version": 1,
            "status": "idle",
            "cursor_turn_id": None,
            "turns": [],
            "discarded_future": [],
            "simulator": {"enabled": False, "config": {}},
        },
        checkpoint={},
    )
    enqueue = AsyncMock()

    async with test_client_factory(user) as client:
        with patch("api.tasks.arq.enqueue_job", enqueue):
            response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/"
                f"{workflow_run.id}/end",
                json={"expected_revision": text_session.revision},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_completed"] is True
    assert payload["state"] == "completed"
    assert payload["session_data"]["status"] == "completed"
    assert payload["gathered_context"]["call_disposition"] == "user_hangup"
    enqueue.assert_awaited_once_with(
        FunctionNames.PROCESS_WORKFLOW_COMPLETION,
        workflow_run.id,
        _job_id=f"workflow-completion-{workflow_run.id}",
    )


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
    draft = await db_session.save_workflow_draft(
        workflow_id=workflow.id,
        workflow_definition=workflow_definition,
        template_context_variables={
            "name": "draft",
            "draft_only": "kept",
            "workflow_run_id": "stale-run-id",
        },
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
                json={"initial_context": {"name": "explicit"}},
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
    assert workflow_run.definition_id == draft.id
    assert workflow_run.initial_context == {
        "name": "explicit",
        "draft_only": "kept",
        "workflow_run_id": workflow_run.id,
        "runtime_configuration": {
            "llm_provider": "openai",
            "llm_model": "gpt-4.1",
        },
    }
    assert "call_duration_seconds" in workflow_run.usage_info
    assert _log_texts(run_payload["logs"], "rtf-bot-text") == [
        "Hello from the workflow tester."
    ]


@pytest.mark.asyncio
async def test_text_chat_pre_call_fetch_hydrates_initial_context_once(
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
                    "prompt": "Help {{customer_name}} on the {{account_tier}} plan.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome {{customer_name}} ({{account_tier}}).",
                    "pre_call_fetch_mode": "always",
                    "pre_call_fetch_url": "https://example.com/customer",
                    "pre_call_fetch_credential_uuid": "credential-uuid",
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
        suffix="pre-call-fetch",
    )
    pre_call_fetch = AsyncMock(
        return_value={
            "workflow_run_id": "fetched-run-id",
            "customer_name": "Fetched",
            "account_tier": "gold",
            "runtime_configuration": {
                "llm_provider": "fetched-provider",
                "llm_model": "fetched-model",
            },
            "mps_correlation_id": "fetched-correlation-id",
        }
    )
    llm_responses = [
        MockLLMService(mock_steps=[], chunk_delay=0.001),
        MockLLMService(
            mock_steps=[MockLLMService.create_text_chunks("How can I help?")],
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
                "api.services.workflow.text_chat_runner.execute_pre_call_fetch",
                new=pre_call_fetch,
            ),
            patch(
                "api.services.managed_model_services.ensure_mps_correlation_id",
                new=AsyncMock(return_value="run-correlation-id"),
            ),
            patch(
                "api.services.workflow.text_chat_runner.db_client.has_active_recordings",
                new=AsyncMock(return_value=False),
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={
                    "initial_context": {
                        "workflow_run_id": "external-run-id",
                        "customer_name": "Explicit",
                        "page_url": "https://dograh.com/pricing",
                    }
                },
            )
            assert create_response.status_code == 200
            created = create_response.json()

            message_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/"
                f"{created['workflow_run_id']}/messages",
                json={
                    "text": "Hello",
                    "expected_revision": created["revision"],
                },
            )
            assert message_response.status_code == 200

    assert (
        created["session_data"]["turns"][0]["assistant_message"]["text"]
        == "Welcome Fetched (gold)."
    )
    assert (
        message_response.json()["session_data"]["turns"][1]["assistant_message"]["text"]
        == "How can I help?"
    )
    pre_call_fetch.assert_awaited_once()
    fetch_kwargs = pre_call_fetch.await_args.kwargs
    assert fetch_kwargs["url"] == "https://example.com/customer"
    assert fetch_kwargs["credential_uuid"] == "credential-uuid"
    assert fetch_kwargs["workflow_id"] == workflow.id
    assert fetch_kwargs["organization_id"] == user.selected_organization_id
    assert (
        fetch_kwargs["call_context_vars"]["workflow_run_id"]
        == created["workflow_run_id"]
    )
    assert fetch_kwargs["call_context_vars"]["customer_name"] == "Explicit"
    assert fetch_kwargs["call_context_vars"]["page_url"] == "https://dograh.com/pricing"
    assert fetch_kwargs["call_context_vars"]["runtime_configuration"] == {
        "llm_provider": "openai",
        "llm_model": "gpt-4.1",
    }
    assert (
        fetch_kwargs["call_context_vars"]["mps_correlation_id"] == "run-correlation-id"
    )

    workflow_run = await db_session.get_workflow_run_by_id(created["workflow_run_id"])
    assert workflow_run is not None
    assert workflow_run.initial_context == {
        "workflow_run_id": workflow_run.id,
        "customer_name": "Fetched",
        "account_tier": "gold",
        "page_url": "https://dograh.com/pricing",
        "runtime_configuration": {
            "llm_provider": "openai",
            "llm_model": "gpt-4.1",
        },
        "mps_correlation_id": "run-correlation-id",
    }


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
                    "extraction_enabled": True,
                    "extraction_prompt": "Extract the customer's details.",
                    "extraction_variables": [
                        {
                            "name": "customer_age",
                            "type": "string",
                            "prompt": "The customer's age.",
                        }
                    ],
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
    extraction_completed = asyncio.Event()

    async def slow_extraction(*_args, **_kwargs):
        # Longer than the text runner's idle-settle window. Background extraction
        # would be omitted from the checkpoint before this returns.
        await asyncio.sleep(0.35)
        extraction_completed.set()
        return {"customer_age": "45"}

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
            patch(
                "api.services.workflow.pipecat_engine_variable_extractor."
                "VariableExtractionManager._perform_extraction",
                new=slow_extraction,
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
    assert extraction_completed.is_set()
    assert payload["checkpoint"]["current_node_id"] == "agent1"
    assert payload["checkpoint"]["gathered_context"]["customer_age"] == "45"
    assert payload["checkpoint"]["gathered_context"]["extracted_variables"] == {
        "customer_age": "45"
    }
    assert payload["gathered_context"]["extracted_variables"] == {"customer_age": "45"}
    assert run_payload["gathered_context"]["extracted_variables"] == {
        "customer_age": "45"
    }
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
async def test_text_chat_end_transition_persists_synchronous_variable_extraction(
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
                    "prompt": "Help the customer.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to the workflow tester.",
                    "extraction_enabled": True,
                    "extraction_prompt": "Extract the customer's details.",
                    "extraction_variables": [
                        {
                            "name": "customer_age",
                            "type": "string",
                            "prompt": "The customer's age.",
                        }
                    ],
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "End",
                    "prompt": "Thank the customer and end the conversation.",
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
                "data": {
                    "label": "End The Call",
                    "condition": "When the customer asks to end the conversation.",
                },
            }
        ],
    }
    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="end-with-extraction",
    )

    llm_responses = [
        MockLLMService(mock_steps=[], chunk_delay=0.001),
        MockLLMService(
            mock_steps=[
                MockLLMService.create_function_call_chunks(
                    "end_the_call",
                    {},
                    tool_call_id="call_end",
                ),
                MockLLMService.create_text_chunks("Thank you for chatting!"),
            ],
            chunk_delay=0.001,
        ),
    ]
    extraction_completed = asyncio.Event()

    async def slow_extraction(*_args, **_kwargs):
        await asyncio.sleep(0.35)
        extraction_completed.set()
        return {"customer_age": "45"}

    enqueue = AsyncMock()
    upload_artifacts = AsyncMock()

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
            patch(
                "api.services.workflow.pipecat_engine_variable_extractor."
                "VariableExtractionManager._perform_extraction",
                new=slow_extraction,
            ),
            patch("api.tasks.arq.enqueue_job", enqueue),
            patch(
                "api.services.workflow.text_chat_session_service."
                "upload_workflow_run_artifacts",
                upload_artifacts,
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            session = create_response.json()

            message_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/"
                f"{session['workflow_run_id']}/messages",
                json={
                    "text": "I am 45. End the call.",
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
    final_turn = payload["session_data"]["turns"][-1]

    assert extraction_completed.is_set()
    assert payload["is_completed"] is True
    assert payload["state"] == "completed"
    assert payload["session_data"]["status"] == "completed"
    assert final_turn["assistant_message"]["text"] == "Thank you for chatting!"
    assert any(event["type"] == "session_end" for event in final_turn["events"])
    assert payload["checkpoint"]["gathered_context"]["extracted_variables"] == {
        "customer_age": "45"
    }
    assert run_payload["gathered_context"]["extracted_variables"] == {
        "customer_age": "45"
    }
    assert (
        run_payload["gathered_context"]["call_disposition"]
        == EndTaskReason.END_CALL.value
    )
    enqueue.assert_awaited_once_with(
        FunctionNames.PROCESS_WORKFLOW_COMPLETION,
        session["workflow_run_id"],
        _job_id=f"workflow-completion-{session['workflow_run_id']}",
    )
    upload_artifacts.assert_awaited_once()


@pytest.mark.asyncio
async def test_text_chat_user_end_persists_variable_extraction_without_transition(
    db_session,
    async_session,
    test_client_factory,
):
    """A chat the user walks away from still extracts the current node's variables.

    Voice calls get this from ``PipecatEngine._end_call``. Text chats tear the
    pipeline down after every turn, so before this the only trigger was a node
    transition -- and a user who simply closes the widget never causes one.
    Anything rendered from ``gathered_context`` afterwards (webhooks especially)
    would ship blanks for variables the node was configured to extract.
    """
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "Help the customer.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to the workflow tester.",
                    "extraction_enabled": True,
                    "extraction_prompt": "Extract the customer's details.",
                    "extraction_variables": [
                        {
                            "name": "ticket_number",
                            "type": "string",
                            "prompt": "The ticket number created for the customer.",
                        }
                    ],
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "End",
                    "prompt": "Thank the customer and end the conversation.",
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
                "data": {
                    "label": "End The Call",
                    "condition": "When the customer asks to end the conversation.",
                },
            }
        ],
    }
    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="user-end-extraction",
    )

    # The assistant answers and stays put -- no transition, so nothing on the
    # turn path extracts anything.
    llm_responses = [
        MockLLMService(mock_steps=[], chunk_delay=0.001),
        MockLLMService(
            mock_steps=[MockLLMService.create_text_chunks("Your ticket is 1054202.")],
            chunk_delay=0.001,
        ),
        MockLLMService(mock_steps=[], chunk_delay=0.001),
    ]

    extraction_calls = []

    async def fake_extraction(_self, variables, *_args, **_kwargs):
        extraction_calls.append([variable.name for variable in variables])
        return {"ticket_number": "1054202"}

    enqueue = AsyncMock()
    upload_artifacts = AsyncMock()

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
            patch(
                "api.services.workflow.pipecat_engine_variable_extractor."
                "VariableExtractionManager._perform_extraction",
                new=fake_extraction,
            ),
            patch("api.tasks.arq.enqueue_job", enqueue),
            patch(
                "api.services.workflow.text_chat_session_service."
                "upload_workflow_run_artifacts",
                upload_artifacts,
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            session = create_response.json()

            message_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/"
                f"{session['workflow_run_id']}/messages",
                json={
                    "text": "Please raise a ticket for my missing shipment.",
                    "expected_revision": session["revision"],
                },
            )
            assert message_response.status_code == 200
            message_payload = message_response.json()

            # Nothing extracted yet: the chat never left the Start node.
            assert message_payload["checkpoint"]["current_node_id"] == "start"
            assert "extracted_variables" not in (
                message_payload["gathered_context"] or {}
            )
            assert extraction_calls == []

            # The user closes the widget.
            end_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/"
                f"{session['workflow_run_id']}/end",
                json={"expected_revision": message_payload["revision"]},
            )
            assert end_response.status_code == 200

    payload = end_response.json()

    assert extraction_calls == [["ticket_number"]]
    assert payload["is_completed"] is True
    assert payload["gathered_context"]["call_disposition"] == "user_hangup"
    assert payload["gathered_context"]["extracted_variables"] == {
        "ticket_number": "1054202"
    }
    assert payload["gathered_context"]["ticket_number"] == "1054202"

    workflow_run = await db_session.get_workflow_run_by_id(session["workflow_run_id"])
    assert workflow_run.gathered_context["extracted_variables"] == {
        "ticket_number": "1054202"
    }


@pytest.mark.asyncio
async def test_text_chat_end_extracts_from_a_turn_left_pending(
    db_session,
    async_session,
    test_client_factory,
):
    """A message left pending by a dead worker still reaches extraction.

    `append_text_chat_user_message` writes the user's text into session_data and
    only folds it into the checkpoint once the turn executes. If the process
    dies in between, the inactivity sweeper (or an `/end` with no
    expected_revision) completes the session off a checkpoint that predates that
    message -- while the transcript, built from session_data, contains it. The
    two must not disagree about what was said.
    """
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "Help the customer.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to the workflow tester.",
                    "extraction_enabled": True,
                    "extraction_prompt": "Extract the customer's details.",
                    "extraction_variables": [
                        {
                            "name": "ticket_number",
                            "type": "string",
                            "prompt": "The ticket number mentioned by the customer.",
                        }
                    ],
                },
            },
            {
                "id": "end",
                "type": "endCall",
                "position": {"x": 0, "y": 200},
                "data": {
                    "name": "End",
                    "prompt": "Thank the customer and end the conversation.",
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
                "data": {
                    "label": "End The Call",
                    "condition": "When the customer asks to end the conversation.",
                },
            }
        ],
    }
    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="pending-turn-extraction",
    )

    llm_responses = [
        MockLLMService(mock_steps=[], chunk_delay=0.001),
        MockLLMService(mock_steps=[], chunk_delay=0.001),
    ]

    captured = {}

    async def fake_extraction(self, variables, *_args, **_kwargs):
        captured["messages"] = [
            dict(message)
            for message in self._context.get_messages()
            if isinstance(message, dict)
        ]
        return {"ticket_number": "1054202"}

    enqueue = AsyncMock()
    upload_artifacts = AsyncMock()

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
            patch(
                "api.services.workflow.pipecat_engine_variable_extractor."
                "VariableExtractionManager._perform_extraction",
                new=fake_extraction,
            ),
            patch("api.tasks.arq.enqueue_job", enqueue),
            patch(
                "api.services.workflow.text_chat_session_service."
                "upload_workflow_run_artifacts",
                upload_artifacts,
            ),
        ):
            create_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions",
                json={},
            )
            assert create_response.status_code == 200
            session = create_response.json()
            run_id = session["workflow_run_id"]

            # The worker appended the pending turn and then died: session_data
            # carries the user's message, the checkpoint does not.
            text_session = await db_session.get_workflow_run_text_session(
                run_id, organization_id=user.selected_organization_id
            )
            await append_text_chat_user_message(
                run_id=run_id,
                text_session=text_session,
                user_text="My ticket is 1054202, please chase it.",
                expected_revision=text_session.revision,
            )
            stranded = await db_session.get_workflow_run_text_session(
                run_id, organization_id=user.selected_organization_id
            )
            assert stranded.session_data["turns"][-1]["status"] == "pending"
            assert not any(
                message.get("role") == "user"
                for message in stranded.checkpoint["messages"]
            )

            end_response = await client.post(
                f"/api/v1/workflow/{workflow.id}/text-chat/sessions/{run_id}/end",
                json={"expected_revision": stranded.revision},
            )
            assert end_response.status_code == 200

    payload = end_response.json()

    assert captured["messages"][-1] == {
        "role": "user",
        "content": "My ticket is 1054202, please chase it.",
    }
    assert payload["gathered_context"]["extracted_variables"] == {
        "ticket_number": "1054202"
    }
    # The transcript already carried this message; extraction now agrees.
    workflow_run = await db_session.get_workflow_run_by_id(run_id)
    assert _log_texts(workflow_run.logs, "rtf-user-transcription") == [
        "My ticket is 1054202, please chase it."
    ]


@pytest.mark.asyncio
async def test_text_chat_end_completes_when_the_checkpoint_cannot_be_parsed(
    db_session,
    async_session,
    test_client_factory,
):
    """A corrupt or legacy checkpoint must not keep the chat from completing.

    Extraction is best effort and runs before the completion write, so anything
    escaping it would leave the run incomplete: no transcript, no completion job
    and no webhook at all -- strictly worse than the blank fields extraction
    exists to prevent. `_deserialize_text_chat_checkpoint_messages` rejects
    unexpected shapes by design, so the parsing has to be inside the handler.
    """
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "Help the customer.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to the workflow tester.",
                    "extraction_enabled": True,
                    "extraction_prompt": "Extract the customer's details.",
                    "extraction_variables": [
                        {
                            "name": "ticket_number",
                            "type": "string",
                            "prompt": "The ticket number.",
                        }
                    ],
                },
            },
        ],
        "edges": [],
    }
    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=workflow_definition,
        suffix="corrupt-checkpoint",
    )
    workflow_run = await db_session.create_workflow_run(
        name="Corrupt checkpoint chat",
        workflow_id=workflow.id,
        mode="textchat",
        user_id=user.id,
        organization_id=user.selected_organization_id,
    )
    await db_session.ensure_workflow_run_text_session(
        workflow_run.id,
        session_data={
            "version": 1,
            "status": "idle",
            "cursor_turn_id": None,
            "turns": [],
            "discarded_future": [],
            "simulator": {"enabled": False, "config": {}},
        },
        # A shape no current writer produces: `messages` is not iterable, so
        # normalization raises before any extraction work begins.
        checkpoint={"current_node_id": "start", "messages": 5},
    )
    enqueue = AsyncMock()

    # Driven at the service layer rather than through `/end`: the route's
    # response builder normalizes the checkpoint a second time and raises on
    # this shape too, which is a separate pre-existing weakness. What matters
    # here is that completion itself is reached and persisted.
    loaded_session = await db_session.get_workflow_run_text_session(
        workflow_run.id, organization_id=user.selected_organization_id
    )
    with patch("api.tasks.arq.enqueue_job", enqueue):
        await complete_text_chat_session(
            run_id=workflow_run.id,
            text_session=loaded_session,
            expected_revision=loaded_session.revision,
        )

    completed_run = await db_session.get_workflow_run_by_id(workflow_run.id)
    assert completed_run.is_completed is True
    assert completed_run.state == "completed"
    assert completed_run.gathered_context["call_disposition"] == "user_hangup"
    assert "extracted_variables" not in completed_run.gathered_context
    # The completion job still runs, so webhooks and billing are not stranded.
    enqueue.assert_awaited_once_with(
        FunctionNames.PROCESS_WORKFLOW_COMPLETION,
        workflow_run.id,
        _job_id=f"workflow-completion-{workflow_run.id}",
    )


def _extraction_workflow_definition() -> dict:
    return {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start",
                    "prompt": "Help the customer.",
                    "is_start": True,
                    "allow_interrupt": False,
                    "add_global_prompt": False,
                    "greeting_type": "text",
                    "greeting": "Welcome to the workflow tester.",
                    "extraction_enabled": True,
                    "extraction_prompt": "Extract the customer's details.",
                    "extraction_variables": [
                        {
                            "name": "ticket_number",
                            "type": "string",
                            "prompt": "The ticket number.",
                        }
                    ],
                },
            },
        ],
        "edges": [],
    }


def _detached_session_snapshot(text_session):
    """A session snapshot that a concurrent write cannot mutate.

    `db_session` hands every caller the same identity-mapped instance, so a
    concurrent append would edit the very object completion is holding and the
    staleness this exercises would never occur. In production each request owns
    its session, so completion really does work from a frozen read.
    """
    return SimpleNamespace(
        revision=text_session.revision,
        checkpoint=copy.deepcopy(text_session.checkpoint),
        session_data=copy.deepcopy(text_session.session_data),
        created_at=text_session.created_at,
        updated_at=text_session.updated_at,
        # The run row is untouched by a session-only write, so it can be shared.
        workflow_run=text_session.workflow_run,
    )


async def _session_with_one_user_turn(db_session, user, workflow, *, user_text: str):
    """A run whose checkpoint already holds a completed user turn."""
    # Re-fetch so the definition relationships are eager-loaded, as the route does.
    loaded_workflow = await db_session.get_workflow(
        workflow.id, organization_id=user.selected_organization_id
    )
    run_inputs = await prepare_workflow_run_inputs(
        db_session,
        loaded_workflow,
        initial_context={},
        use_draft=True,
        include_template_context=True,
    )
    workflow_run = await db_session.create_workflow_run(
        name="Concurrent completion chat",
        workflow_id=workflow.id,
        mode="textchat",
        user_id=user.id,
        organization_id=user.selected_organization_id,
        initial_context=run_inputs.initial_context,
        definition_id=run_inputs.definition_id,
        use_draft=run_inputs.use_draft,
    )
    await db_session.ensure_workflow_run_text_session(
        workflow_run.id,
        session_data={
            "version": 1,
            "status": "idle",
            "cursor_turn_id": None,
            "turns": [
                {
                    "id": "turn-1",
                    "status": "completed",
                    "created_at": "2026-09-16T06:00:00+00:00",
                    "user_message": {
                        "text": user_text,
                        "created_at": "2026-09-16T06:00:00+00:00",
                    },
                    "assistant_message": None,
                    "events": [],
                }
            ],
            "discarded_future": [],
            "simulator": {"enabled": False, "config": {}},
        },
        checkpoint={
            "version": 1,
            "anchor_turn_id": "turn-1",
            "current_node_id": "start",
            "messages": [{"role": "user", "content": user_text}],
            "gathered_context": {},
            "tool_state": {},
        },
    )
    return workflow_run


@pytest.mark.asyncio
async def test_text_chat_end_without_pinned_revision_keeps_a_concurrent_turn(
    db_session,
    async_session,
    test_client_factory,
):
    """An unpinned end must not overwrite state committed while it extracts.

    `expected_revision` defaults to None on both end-request schemas, so the
    write used to run unguarded. Extraction waits on an LLM between reading the
    session and writing it back, which is easily long enough for a message or
    rewind to commit -- and that turn would be silently erased.
    """
    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=_extraction_workflow_definition(),
        suffix="concurrent-unpinned",
    )
    workflow_run = await _session_with_one_user_turn(
        db_session, user, workflow, user_text="My ticket is 1054202."
    )

    extraction_started = asyncio.Event()
    release_extraction = asyncio.Event()

    async def blocking_extraction(_self, _variables, *_args, **_kwargs):
        extraction_started.set()
        await release_extraction.wait()
        return {"ticket_number": "1054202"}

    loaded = await db_session.get_workflow_run_text_session(
        workflow_run.id, organization_id=user.selected_organization_id
    )

    with (
        patch(
            "api.services.workflow.text_chat_runner.create_llm_service",
            side_effect=lambda *a, **k: MockLLMService(
                mock_steps=[], chunk_delay=0.001
            ),
        ),
        patch(
            "api.services.workflow.pipecat_engine_variable_extractor."
            "VariableExtractionManager._perform_extraction",
            new=blocking_extraction,
        ),
        patch("api.tasks.arq.enqueue_job", AsyncMock()),
        patch(
            "api.services.workflow.text_chat_session_service."
            "upload_workflow_run_artifacts",
            AsyncMock(),
        ),
    ):
        completion = asyncio.create_task(
            complete_text_chat_session(
                run_id=workflow_run.id,
                text_session=_detached_session_snapshot(loaded),
                expected_revision=None,
            )
        )
        await asyncio.wait_for(extraction_started.wait(), timeout=5)

        # A message lands while completion is blocked on the LLM.
        concurrent = await db_session.get_workflow_run_text_session(
            workflow_run.id, organization_id=user.selected_organization_id
        )
        await append_text_chat_user_message(
            run_id=workflow_run.id,
            text_session=concurrent,
            user_text="One more thing.",
            expected_revision=concurrent.revision,
        )

        release_extraction.set()
        result = await asyncio.wait_for(completion, timeout=10)

    # The concurrent turn survived; completion rebuilt itself around it.
    turn_texts = [
        (turn.get("user_message") or {}).get("text")
        for turn in result.session_data["turns"]
    ]
    assert turn_texts == ["My ticket is 1054202.", "One more thing."]
    assert result.session_data["status"] == "completed"

    completed_run = await db_session.get_workflow_run_by_id(workflow_run.id)
    assert completed_run.is_completed is True
    assert completed_run.gathered_context["extracted_variables"] == {
        "ticket_number": "1054202"
    }


@pytest.mark.asyncio
async def test_text_chat_end_with_pinned_revision_reports_a_concurrent_turn(
    db_session,
    async_session,
    test_client_factory,
):
    """A pinned end that loses the race still reports the conflict."""
    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=_extraction_workflow_definition(),
        suffix="concurrent-pinned",
    )
    workflow_run = await _session_with_one_user_turn(
        db_session, user, workflow, user_text="My ticket is 1054202."
    )

    extraction_started = asyncio.Event()
    release_extraction = asyncio.Event()

    async def blocking_extraction(_self, _variables, *_args, **_kwargs):
        extraction_started.set()
        await release_extraction.wait()
        return {"ticket_number": "1054202"}

    loaded = await db_session.get_workflow_run_text_session(
        workflow_run.id, organization_id=user.selected_organization_id
    )
    pinned_revision = loaded.revision

    with (
        patch(
            "api.services.workflow.text_chat_runner.create_llm_service",
            side_effect=lambda *a, **k: MockLLMService(
                mock_steps=[], chunk_delay=0.001
            ),
        ),
        patch(
            "api.services.workflow.pipecat_engine_variable_extractor."
            "VariableExtractionManager._perform_extraction",
            new=blocking_extraction,
        ),
        patch("api.tasks.arq.enqueue_job", AsyncMock()),
    ):
        completion = asyncio.create_task(
            complete_text_chat_session(
                run_id=workflow_run.id,
                text_session=_detached_session_snapshot(loaded),
                expected_revision=pinned_revision,
            )
        )
        await asyncio.wait_for(extraction_started.wait(), timeout=5)

        concurrent = await db_session.get_workflow_run_text_session(
            workflow_run.id, organization_id=user.selected_organization_id
        )
        await append_text_chat_user_message(
            run_id=workflow_run.id,
            text_session=concurrent,
            user_text="One more thing.",
            expected_revision=concurrent.revision,
        )

        release_extraction.set()
        with pytest.raises(TextChatSessionRevisionConflictError):
            await asyncio.wait_for(completion, timeout=10)

    # Nothing was committed: the chat is still live and the turn intact.
    still_running = await db_session.get_workflow_run_by_id(workflow_run.id)
    assert still_running.is_completed is False
    reloaded = await db_session.get_workflow_run_text_session(
        workflow_run.id, organization_id=user.selected_organization_id
    )
    assert reloaded.session_data["turns"][-1]["user_message"]["text"] == (
        "One more thing."
    )


@pytest.mark.asyncio
async def test_final_extraction_refuses_a_run_from_another_organization(
    db_session,
    async_session,
    test_client_factory,
):
    """The helper validates the run it loads against the caller's organization.

    `get_workflow_run_with_context` is the unscoped system-caller read, and the
    workflow_id check proves nothing about ownership because it comes from the
    same row. api/AGENTS.md requires every org-scoped read to be filtered or
    validated by organization_id.
    """
    user, workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=_extraction_workflow_definition(),
        suffix="org-scope-owner",
    )
    # A real second organization, configured exactly like the first. A bogus id
    # would be refused anyway for want of a model configuration, which would
    # let this pass without the ownership check ever running.
    other_user, _other_workflow = await _create_user_and_workflow(
        db_session,
        async_session,
        workflow_definition=_extraction_workflow_definition(),
        suffix="org-scope-intruder",
    )
    workflow_run = await _session_with_one_user_turn(
        db_session, user, workflow, user_text="My ticket is 1054202."
    )
    text_session = await db_session.get_workflow_run_text_session(
        workflow_run.id, organization_id=user.selected_organization_id
    )

    extraction_calls = []

    async def fake_extraction(_self, _variables, *_args, **_kwargs):
        extraction_calls.append(True)
        return {"ticket_number": "1054202"}

    with (
        patch(
            "api.services.workflow.text_chat_runner.create_llm_service",
            side_effect=lambda *a, **k: MockLLMService(
                mock_steps=[], chunk_delay=0.001
            ),
        ),
        patch(
            "api.services.workflow.pipecat_engine_variable_extractor."
            "VariableExtractionManager._perform_extraction",
            new=fake_extraction,
        ),
    ):
        # The run's real organization: extraction proceeds.
        allowed = await extract_text_chat_final_variables(
            workflow_run_id=workflow_run.id,
            workflow_id=workflow.id,
            organization_id=user.selected_organization_id,
            checkpoint=text_session.checkpoint,
            session_data=text_session.session_data,
        )

        # Another real organization reaching the same run: refused, without
        # touching the LLM, even though its own configuration would work.
        refused = await extract_text_chat_final_variables(
            workflow_run_id=workflow_run.id,
            workflow_id=workflow.id,
            organization_id=other_user.selected_organization_id,
            checkpoint=text_session.checkpoint,
            session_data=text_session.session_data,
        )

    assert allowed["extracted_variables"] == {"ticket_number": "1054202"}
    assert refused == {}
    assert len(extraction_calls) == 1


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

        end_response = await other_client.post(
            f"/api/v1/workflow/{workflow.id}/text-chat/sessions/"
            f"{created['workflow_run_id']}/end",
            json={"expected_revision": created["revision"]},
        )
        assert end_response.status_code == 404


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
    await async_session.execute(
        organization_users_association.insert().values(
            user_id=user.id,
            organization_id=org_a.id,
        )
    )

    user_configuration = EffectiveAIModelConfiguration.model_validate(
        USER_CONFIGURATION
    )
    await db_session.upsert_configuration(
        org_a.id,
        OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
        convert_legacy_ai_model_configuration_to_v2(user_configuration).model_dump(
            mode="json",
            exclude_none=True,
        ),
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
