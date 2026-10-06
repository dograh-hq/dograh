"""Transferred services must retain the original run's managed authorization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.services.configuration import model_connections
from api.services.pipecat import agent_runtime_factory as factory_module
from api.services.pipecat.agent_runtime_factory import (
    AgentBuildError,
    AgentRuntimeFactory,
)
from api.tests.test_model_connections import catalog as catalog_fixture
from api.tests.test_model_connections import connection, pipeline

catalog = catalog_fixture


@pytest.fixture
def factory(monkeypatch):
    instance = AgentRuntimeFactory(
        organization_id=1,
        workflow_run_id=51,
        call_worker=None,
        audio_config=None,
        callbacks_factory=lambda visit: None,
        mps_correlation_id="authorized-correlation",
    )
    definition = SimpleNamespace(
        id=19,
        version_number=1,
        status="published",
        workflow_json={"nodes": [], "edges": []},
        workflow_configurations={},
    )
    monkeypatch.setattr(
        instance,
        "resolve_destination",
        AsyncMock(return_value=(SimpleNamespace(name="Destination"), definition)),
    )
    monkeypatch.setattr(instance, "attach", AsyncMock())
    monkeypatch.setattr(
        factory_module,
        "WorkflowGraph",
        Mock(return_value=SimpleNamespace(uses_variable_extraction=lambda: False)),
    )
    monkeypatch.setattr(
        factory_module, "create_llm_service", Mock(return_value=object())
    )
    monkeypatch.setattr(
        factory_module, "create_tts_service", Mock(return_value=object())
    )
    return instance, definition


@pytest.mark.asyncio
async def test_transfer_uses_destination_settings_and_original_pooled_key(
    catalog, factory
):
    row = connection(keys=["secret-a", "secret-b"])
    catalog(row, default=True)
    root = await model_connections.resolve_model_configuration(
        1,
        api_override={"llm": {"settings": {"temperature": 0.9}}},
        preferred_dograh_key="secret-b",
    )
    instance, definition = factory
    instance._prepared_model_configuration = root.effective
    definition.workflow_configurations = {
        "model_configuration_override": {"llm": {"settings": {"temperature": 0.1}}}
    }
    runtime = await instance.build(workflow_id=9)
    assert runtime.user_config.llm.temperature == 0.1
    assert (
        runtime.user_config.llm.api_key == runtime.user_config.tts.api_key == "secret-b"
    )
    assert (
        factory_module.create_llm_service.call_args.kwargs["correlation_id"]
        == "authorized-correlation"
    )


@pytest.mark.asyncio
async def test_transfer_cannot_use_a_different_dograh_key(catalog, factory):
    row = connection(keys="root-key")
    catalog(row, default=True)
    root = await model_connections.resolve_model_configuration(1)
    destination = connection(keys="unrelated-key")
    named = catalog(destination, configuration=pipeline(destination))
    instance, definition = factory
    instance._prepared_model_configuration = root.effective
    definition.workflow_configurations = {
        "model_configuration_override": {"model_configuration_uuid": named.uuid}
    }
    with pytest.raises(AgentBuildError) as error:
        await instance.build(workflow_id=9)
    assert error.value.reason == "destination_model_configuration_invalid"
    assert "unrelated-key" not in str(error.value)
    factory_module.create_llm_service.assert_not_called()
    instance.attach.assert_not_awaited()
