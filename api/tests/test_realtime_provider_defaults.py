from api.services.pipecat.openai_realtime import create_openai_realtime_llm_service
from api.services.configuration.defaults import DEFAULT_SERVICE_PROVIDERS
from api.services.configuration.registry import REGISTRY, ServiceType
from api.services.workflow.pipecat_engine import PipecatEngine
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
