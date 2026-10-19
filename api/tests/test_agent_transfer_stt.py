"""Transfer language changes must use Flux Configure, never a new socket."""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.flux.stt_base import DeepgramFluxSTTSettings
from pipecat.services.dograh.flux.stt import DograhFluxSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.enums import EndTaskReason

from api.services.configuration import registry
from api.services.pipecat.agent_runtime_factory import AgentRuntimeFactory
from api.tests.test_agent_transfer import (
    TRANSFER_TOOL_UUID,
    TransferAgentTool,
    TransferHarness,
    build_agent_workflow,
    run_transfer,
)
from pipecat.tests import MockLLMService


@pytest.fixture(params=[DeepgramFluxSTTService, DograhFluxSTTService])
def stt(request):
    service = request.param(
        api_key="test-key",
        settings=DeepgramFluxSTTSettings(
            model="flux-general-multi",
            language_hints=[Language.EN],
            keyterm=["Adega Gaucha"],
            eot_threshold=0.7,
            eager_eot_threshold=0.5,
            eot_timeout_ms=3000,
        ),
    )
    # Keep the real settings and Configure code; replace only socket I/O.
    service._transport_is_active = Mock(return_value=True)
    service._transport_send_json = AsyncMock()
    service._connect = AsyncMock()
    service._disconnect = AsyncMock()
    service._request_reconnect = AsyncMock()
    config_type = (
        registry.DeepgramSTTConfiguration
        if request.param is DeepgramFluxSTTService
        else registry.DograhSTTService
    )
    service.test_config = config_type(
        model="flux-general-multi"
        if request.param is DeepgramFluxSTTService
        else "default",
        api_key="test-key",
        language="en",
        language_hints=[],
    )
    return service


def factory_for(stt):
    return AgentRuntimeFactory(
        organization_id=1,
        workflow_run_id=1,
        call_worker=None,
        audio_config=None,
        callbacks_factory=lambda visit: None,
        stt=stt,
        run_model_configuration=SimpleNamespace(stt=getattr(stt, "test_config", None)),
    )


def destination(stt, language, hints=None):
    return SimpleNamespace(
        visit_id="destination",
        user_config=SimpleNamespace(
            stt=stt.test_config.model_copy(
                update={"language": language, "language_hints": hints}
            )
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "hints", "expected"),
    [
        ("pt-BR", ["en", "pt", "en-GB"], ["pt", "en"]),
        ("multi", ["pt", "en"], ["pt", "en"]),
        ("multi", [], []),
        (None, None, []),
        ("en", [], None),
    ],
)
async def test_transfer_updates_only_language_hints(stt, language, hints, expected):
    original_settings = asdict(stt._settings)
    changed = await factory_for(stt).retune_stt_for(destination(stt, language, hints))

    if expected is None:
        assert not changed
        stt._transport_send_json.assert_not_awaited()
    else:
        assert changed
        stt._transport_send_json.assert_awaited_once_with(
            {"type": "Configure", "language_hints": expected}
        )
        original_settings["language_hints"] = [Language(code) for code in expected]
    assert asdict(stt._settings) == original_settings
    assert stt._api_key == "test-key"
    stt._connect.assert_not_awaited()
    stt._disconnect.assert_not_awaited()
    stt._request_reconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_transfers_restore_source_language_on_same_connection(stt):
    factory = factory_for(stt)
    await factory.retune_stt_for(destination(stt, "pt", ["en"]))
    # A second handoff can arrive before the previous Configure is acked.
    await factory.retune_stt_for(destination(stt, "en"))
    await stt._on_configure_acked()
    assert [call.args[0] for call in stt._transport_send_json.await_args_list] == [
        {"type": "Configure", "language_hints": ["pt", "en"]},
        {"type": "Configure", "language_hints": ["en"]},
    ]
    assert stt._settings.language_hints == [Language.EN]
    stt._request_reconnect.assert_not_awaited()
    stt._disconnect.assert_not_awaited()
    stt._connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_english_only_flux_is_not_reconfigured(stt):
    stt._settings.model = "flux-general-en"
    stt.test_config.provider = "deepgram"
    stt.test_config.model = "flux-general-en"
    assert not await factory_for(stt).retune_stt_for(destination(stt, "pt"))
    stt._transport_send_json.assert_not_awaited()
    stt._request_reconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_transcriber_is_not_reconfigured():
    assert not await factory_for(None).retune_stt_for(SimpleNamespace(user_config=None))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["completed", "activation_failed", "error", "timeout", "hangup"]
)
async def test_stt_updates_after_commit_before_destination_opening(
    monkeypatch, stt, outcome
):
    monkeypatch.setattr(
        "api.services.workflow.agent_transfer.TRANSFER_MIN_HOLD_SECONDS", 0
    )
    harness = TransferHarness()
    await harness.build(
        source_llm=MockLLMService(
            mock_steps=[
                MockLLMService.create_function_call_chunks("transfer_to_billing", {}),
                MockLLMService.create_text_chunks("Let me keep helping you."),
            ],
            chunk_delay=0,
        ),
        destination_llm=MockLLMService(mock_steps=[], chunk_delay=0),
        source_workflow=build_agent_workflow(
            name="Reception", greeting=None, tool_uuids=[TRANSFER_TOOL_UUID]
        ),
        destination_workflow=build_agent_workflow(name="Billing", greeting="Olá!"),
    )
    engine = harness.engine
    factory = engine._agent_factory
    factory._stt = stt
    factory._run_model_configuration = SimpleNamespace(stt=stt.test_config)
    build = factory.build

    async def build_destination(**kwargs):
        runtime = await build(**kwargs)
        runtime.user_config = destination(stt, "pt-BR", ["en"]).user_config
        return runtime

    monkeypatch.setattr(factory, "build", build_destination)
    retune = AsyncMock(wraps=factory.retune_stt_for)
    monkeypatch.setattr(factory, "retune_stt_for", retune)
    await harness.start()
    source = engine.active_agent
    original_settings = asdict(stt._settings)
    activate = engine.activate_agent

    async def activate_destination(runtime, *, timeout):
        if runtime is not source and outcome == "activation_failed":
            return False
        return await activate(runtime, timeout=timeout)

    monkeypatch.setattr(engine, "activate_agent", activate_destination)
    configure_started = asyncio.Event()
    send_states = []

    async def send_configure(message):
        # Assert these outside the provider call: the coordinator deliberately
        # catches provider exceptions, including an AssertionError in a mock.
        send_states.append(
            (
                engine.active_agent is not source,
                engine.transfer_coordinator.hold_audio_active,
                engine.agent_can_generate(engine.active_agent),
                list(harness.destination_tts.received_texts),
            )
        )
        configure_started.set()
        if outcome == "error":
            raise RuntimeError("Configure send failed")
        if outcome == "timeout":
            raise TimeoutError("Provider settings update timed out")
        if outcome == "hangup":
            await asyncio.Event().wait()

    stt._transport_send_json.side_effect = send_configure
    transfer_task = asyncio.create_task(
        run_transfer(harness, tool=TransferAgentTool(message=None))
    )
    try:
        if outcome == "hangup":
            await asyncio.wait_for(configure_started.wait(), 5)
            await engine.end_call_with_reason(EndTaskReason.USER_HANGUP.value)
        await asyncio.wait_for(transfer_task, 10)
        if outcome == "activation_failed":
            assert engine.active_agent is source
            assert engine.transfer_coordinator.completed[-1]["outcome"] == outcome
            retune.assert_not_awaited()
            stt._transport_send_json.assert_not_awaited()
            assert asdict(stt._settings) == original_settings
        else:
            retune.assert_awaited_once_with(engine.active_agent)
            stt._transport_send_json.assert_awaited_once_with(
                {"type": "Configure", "language_hints": ["pt", "en"]}
            )
            assert send_states == [(True, True, False, [])]
            if outcome == "hangup":
                assert engine.transfer_coordinator.completed[-1]["outcome"] == (
                    EndTaskReason.USER_HANGUP.value
                )
                assert not harness.destination_tts.received_texts
            else:
                assert (
                    engine.transfer_coordinator.completed[-1]["outcome"] == "completed"
                )
                async with asyncio.timeout(5):
                    while "Olá!" not in harness.destination_tts.received_texts:
                        await asyncio.sleep(0.01)
            assert source.retired
        assert engine.is_call_disposed() == (outcome == "hangup")
        stt._request_reconnect.assert_not_awaited()
        stt._disconnect.assert_not_awaited()
        stt._connect.assert_not_awaited()
    finally:
        if not transfer_task.done():
            transfer_task.cancel()
        await asyncio.gather(transfer_task, return_exceptions=True)
        await harness.stop()
