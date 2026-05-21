import json
from unittest.mock import AsyncMock

import pytest
from types import SimpleNamespace

from pipecat.services.openai.realtime.events import ConversationItemCreateEvent

from api.services.pipecat.openai_realtime import create_openai_realtime_llm_service
from api.services.configuration.defaults import DEFAULT_SERVICE_PROVIDERS
from api.services.configuration.registry import REGISTRY, ServiceType
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.pipecat.realtime.openai_realtime import (
    DograhOpenAIRealtimeLLMService,
)
from api.services.pipecat.service_factory import create_realtime_llm_service
from pipecat.utils.async_tool_cancellation import CANCEL_ASYNC_TOOL_NAME


def test_openai_realtime_is_exposed_in_default_configurations():
    assert "openai_realtime" in REGISTRY[ServiceType.REALTIME]
    assert DEFAULT_SERVICE_PROVIDERS["realtime"] == "google_realtime"


def test_openai_realtime_enables_async_tool_cancellation():
    service = create_openai_realtime_llm_service(
        api_key="test",
        model="gpt-4o-realtime-preview",
    )

    assert service._enable_async_tool_cancellation is True


def test_openai_realtime_registers_async_cancellation_tool_dynamically():
    service = create_openai_realtime_llm_service(
        api_key="test",
        model="gpt-4o-realtime-preview",
    )

    async def slow_tool(_params):
        pass

    service.register_function(
        "slow_tool",
        slow_tool,
        cancel_on_interruption=False,
    )
    engine = PipecatEngine(
        llm=service,
        context=None,
        workflow=None,
        call_context_vars={},
    )
    engine.ensure_async_tool_cancellation_enabled()

    assert service._async_tool_cancellation_enabled is True
    assert CANCEL_ASYNC_TOOL_NAME in service._functions


def test_openai_realtime_factory_uses_main_style_service():
    user_config = SimpleNamespace(
        realtime=SimpleNamespace(
            provider="openai_realtime",
            model="gpt-4o-realtime-preview",
            api_key="test",
            voice="alloy",
        )
    )
    service = create_realtime_llm_service(user_config, audio_config=SimpleNamespace())

    assert isinstance(service, DograhOpenAIRealtimeLLMService)
    assert service._enable_async_tool_cancellation is True


class FakeContext:
    def __init__(self, messages):
        self._messages = messages

    def get_messages(self):
        return self._messages


@pytest.mark.asyncio
async def test_openai_realtime_sends_function_call_output_as_plain_string():
    service = create_openai_realtime_llm_service(
        api_key="test",
        model="gpt-4o-realtime-preview",
    )
    service.send_client_event = AsyncMock()

    await service._send_tool_result("call_sync", '{"foo":"bar"}')

    service.send_client_event.assert_awaited_once()
    event = service.send_client_event.await_args.args[0]
    assert isinstance(event, ConversationItemCreateEvent)
    assert event.item.type == "function_call_output"
    assert event.item.call_id == "call_sync"
    assert event.item.output == '{"foo":"bar"}'


@pytest.mark.asyncio
async def test_openai_realtime_sends_sync_tool_result_to_api():
    service = create_openai_realtime_llm_service(
        api_key="test",
        model="gpt-4o-realtime-preview",
    )
    service._context = FakeContext(
        [
            {
                "role": "tool",
                "tool_call_id": "call_sync",
                "content": '{"weather":"sunny"}',
            }
        ]
    )
    service._send_tool_result = AsyncMock()
    service._create_response = AsyncMock()

    await service._process_completed_function_calls(send_new_results=True)

    service._send_tool_result.assert_awaited_once_with(
        "call_sync",
        '{"weather":"sunny"}',
    )
    service._create_response.assert_awaited_once()
    assert "call_sync" in service._completed_tool_calls


@pytest.mark.asyncio
async def test_openai_realtime_does_not_send_async_running_marker_as_tool_result():
    service = create_openai_realtime_llm_service(
        api_key="test",
        model="gpt-4o-realtime-preview",
    )
    service._context = FakeContext(
        [
            {
                "role": "tool",
                "tool_call_id": "call_async",
                "content": json.dumps(
                    {
                        "type": "async_tool",
                        "status": "running",
                        "tool_call_id": "call_async",
                    }
                ),
            }
        ]
    )
    service._send_tool_result = AsyncMock()
    service._create_response = AsyncMock()

    await service._process_completed_function_calls(send_new_results=True)

    service._send_tool_result.assert_not_awaited()
    service._create_response.assert_not_awaited()
    assert "call_async" not in service._completed_tool_calls


@pytest.mark.asyncio
async def test_openai_realtime_sends_async_final_result_to_api():
    service = create_openai_realtime_llm_service(
        api_key="test",
        model="gpt-4o-realtime-preview",
    )
    final_result = json.dumps({"answer": "42"})
    service._context = FakeContext(
        [
            {
                "role": "tool",
                "tool_call_id": "call_async",
                "content": json.dumps(
                    {
                        "type": "async_tool",
                        "status": "running",
                        "tool_call_id": "call_async",
                    }
                ),
            },
            {
                "role": "developer",
                "content": json.dumps(
                    {
                        "type": "async_tool",
                        "status": "finished",
                        "tool_call_id": "call_async",
                        "result": final_result,
                    }
                ),
            },
        ]
    )
    service._send_tool_result = AsyncMock()
    service._create_response = AsyncMock()

    await service._process_completed_function_calls(send_new_results=True)

    service._send_tool_result.assert_awaited_once_with("call_async", final_result)
    service._create_response.assert_awaited_once()
    assert "call_async" in service._completed_tool_calls


@pytest.mark.asyncio
async def test_openai_realtime_initial_context_marks_finished_async_result_completed():
    service = create_openai_realtime_llm_service(
        api_key="test",
        model="gpt-4o-realtime-preview",
    )
    service._context = FakeContext(
        [
            {
                "role": "developer",
                "content": json.dumps(
                    {
                        "type": "async_tool",
                        "status": "finished",
                        "tool_call_id": "call_async",
                        "result": "done",
                    }
                ),
            }
        ]
    )
    service._send_tool_result = AsyncMock()
    service._create_response = AsyncMock()

    await service._process_completed_function_calls(send_new_results=False)

    service._send_tool_result.assert_not_awaited()
    service._create_response.assert_not_awaited()
    assert "call_async" in service._completed_tool_calls
