"""Provider-specific settings updates share the call-start configuration path."""

from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.services.google.stt import GoogleSTTService, GoogleSTTSettings
from pipecat.transcriptions.language import Language

from api.services.configuration import registry
from api.services.pipecat import service_factory
from api.services.pipecat.agent_runtime_factory import AgentRuntimeFactory


@pytest.mark.parametrize(
    ("config_name", "service_name", "settings", "expected"),
    [
        (
            "DeepgramSTTConfiguration",
            "DeepgramSTTService",
            {"language": "pt"},
            {"language": "pt"},
        ),
        (
            "GoogleSTTConfiguration",
            "GoogleSTTService",
            {"language": "pt-BR"},
            {"languages": [Language.PT_BR], "language_codes": None},
        ),
        (
            "GoogleSTTConfiguration",
            "GoogleSTTService",
            {"language": "cmn-Hans-CN"},
            {"languages": [], "language_codes": ["cmn-Hans-CN"]},
        ),
        (
            "CartesiaSTTConfiguration",
            "CartesiaSTTService",
            {"model": "ink-whisper", "language": "pt"},
            {"language": "pt"},
        ),
        (
            "SarvamSTTConfiguration",
            "SarvamSTTService",
            {"language": "hi-IN"},
            {"language": Language.HI_IN},
        ),
        (
            "SarvamSTTConfiguration",
            "SarvamSTTService",
            {"language": "unknown"},
            {"language": None},
        ),
        (
            "SarvamSTTConfiguration",
            "SarvamSTTService",
            {"language": "od-IN"},
            {"language": Language.OR_IN},
        ),
        (
            "SpeachesSTTConfiguration",
            "SpeachesSTTService",
            {"language": "pt"},
            {"language": "pt"},
        ),
        (
            "HuggingFaceSTTConfiguration",
            "HuggingFaceSTTService",
            {"return_timestamps": True},
            {"return_timestamps": True},
        ),
        (
            "AssemblyAISTTConfiguration",
            "AssemblyAISTTService",
            {"language": "pt"},
            {"language_codes": [Language.PT]},
        ),
        (
            "SonioxSTTConfiguration",
            "SonioxSTTService",
            {"language": "pt"},
            {"language_hints": [Language.PT]},
        ),
        (
            "SpeechmaticsSTTConfiguration",
            "SpeechmaticsSTTService",
            {"language": "pt"},
            {"language": "pt"},
        ),
        (
            "AzureSpeechSTTConfiguration",
            "AzureSTTService",
            {"language": "pt-BR"},
            {"language": Language.PT_BR},
        ),
        (
            "SmallestAISTTConfiguration",
            "SmallestSTTService",
            {"language": "pt"},
            {"language": Language.PT},
        ),
        (
            "ElevenlabsSTTConfiguration",
            "ElevenLabsRealtimeSTTService",
            {"language": "pt"},
            {"language": Language.PT},
        ),
        (
            "ElevenlabsSTTConfiguration",
            "ElevenLabsRealtimeSTTService",
            {"language": "auto"},
            {"language": None},
        ),
    ],
)
def test_startup_and_transfer_translate_settings_consistently(
    config_name, service_name, settings, expected
):
    config = getattr(registry, config_name)(api_key="test-key", **settings)
    with patch.object(service_factory, service_name) as constructor:
        service_factory.create_stt_service(
            SimpleNamespace(stt=config), SimpleNamespace(transport_in_sample_rate=16000)
        )
    initial_settings = constructor.call_args.kwargs["settings"]
    stt = SimpleNamespace(_settings=initial_settings)
    delta = service_factory.get_stt_settings_update(stt, config, config)
    assert delta.given_fields() == expected
    for key, value in expected.items():
        # The enum-language path explicitly clears this override on transfer.
        if key != "language_codes" or value is not None:
            assert getattr(initial_settings, key) == value


def factory_for(stt, config):
    return AgentRuntimeFactory(
        organization_id=1,
        workflow_run_id=1,
        call_worker=None,
        audio_config=None,
        callbacks_factory=lambda visit: None,
        stt=stt,
        run_model_configuration=SimpleNamespace(stt=config),
    )


async def retune(factory, config):
    return await factory.retune_stt_for(
        SimpleNamespace(visit_id="destination", user_config=SimpleNamespace(stt=config))
    )


def create_stt(config):
    return service_factory.create_stt_service(
        SimpleNamespace(stt=config),
        SimpleNamespace(transport_in_sample_rate=16000),
        keyterms=["Adega Gaucha"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config_name",
    ["DeepgramSTTConfiguration", "CartesiaSTTConfiguration", "SonioxSTTConfiguration"],
)
async def test_language_update_uses_provider_restart_handler(config_name):
    config = getattr(registry, config_name)(api_key="test-key", language="en")
    stt = create_stt(config)
    stt._request_reconnect = AsyncMock()
    before = asdict(stt._settings)
    factory = factory_for(stt, config)
    target = config.model_copy(update={"language": "pt"})
    assert await retune(factory, target)
    stt._request_reconnect.assert_awaited_once()
    field = "language_hints" if config_name == "SonioxSTTConfiguration" else "language"
    before[field] = [Language.PT] if field == "language_hints" else "pt"
    assert asdict(stt._settings) == before
    assert not await retune(factory, target)
    stt._request_reconnect.assert_awaited_once()
    assert await retune(factory, config)
    assert stt._request_reconnect.await_count == 2


@pytest.mark.asyncio
async def test_assemblyai_updates_active_session_without_reconnect():
    config = registry.AssemblyAISTTConfiguration(api_key="test-key", language="en")
    stt = create_stt(config)
    stt._send_update_configuration = AsyncMock()
    stt._disconnect = AsyncMock()
    stt._connect = AsyncMock()
    assert await retune(
        factory_for(stt, config), config.model_copy(update={"language": "pt"})
    )
    stt._send_update_configuration.assert_awaited_once_with(language_codes=["pt"])
    stt._disconnect.assert_not_awaited()
    stt._connect.assert_not_awaited()
    assert stt._settings.keyterms_prompt == ["Adega Gaucha"]


@pytest.mark.asyncio
async def test_google_clears_alternate_language_representation_on_each_transfer():
    config = registry.GoogleSTTConfiguration(language="en-US")
    # Exercise real Google settings handling without loading cloud credentials.
    stt = GoogleSTTService.__new__(GoogleSTTService)
    stt._name = "GoogleSTT"
    stt._settings = GoogleSTTSettings(languages=[Language.EN_US], language_codes=None)
    stt.set_usable = AsyncMock()
    stt._reconnect_if_needed = AsyncMock()
    factory = factory_for(stt, config)
    assert await retune(factory, config.model_copy(update={"language": "cmn-Hans-CN"}))
    assert stt._get_language_codes() == ["cmn-Hans-CN"]
    assert await retune(factory, config.model_copy(update={"language": "pt-BR"}))
    assert stt._get_language_codes() == ["pt-BR"]
    assert stt._settings.language_codes is None
    assert stt._reconnect_if_needed.await_count == 2


@pytest.mark.asyncio
async def test_huggingface_updates_non_language_setting_on_next_request():
    config = registry.HuggingFaceSTTConfiguration(api_key="test-key")
    stt = create_stt(config)
    factory = factory_for(stt, config)
    assert await retune(factory, config.model_copy(update={"return_timestamps": True}))
    assert stt._build_payload(b"audio")["parameters"]["return_timestamps"] is True
    assert await retune(factory, config)
    assert stt._build_payload(b"audio")["parameters"]["return_timestamps"] is False


@pytest.mark.asyncio
async def test_speaches_applies_language_to_next_transcription_request():
    config = registry.SpeachesSTTConfiguration(api_key="test-key")
    stt = create_stt(config)
    stt._client.audio.transcriptions.create = AsyncMock(
        return_value=SimpleNamespace(text="Sim")
    )
    assert await retune(
        factory_for(stt, config), config.model_copy(update={"language": "pt"})
    )
    await stt._transcribe(b"audio")
    assert stt._client.audio.transcriptions.create.call_args.kwargs["language"] == "pt"
    await stt._client.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "cartesia"),
        ("model", "flux-general-multi"),
        ("api_key", "other-key"),
        ("base_url", "https://api.eu.deepgram.com"),
    ],
)
def test_connection_changes_do_not_apply_settings(field, value):
    config = registry.DeepgramSTTConfiguration(api_key="test-key", language="en")
    target = config.model_copy(update={field: value, "language": "pt"})
    stt = SimpleNamespace(_settings=service_factory.DeepgramSTTSettings(language="en"))
    assert service_factory.get_stt_settings_update(stt, config, target) is None
    assert stt._settings.language == "en"


def test_key_pool_comparison_does_not_select_new_credentials():
    config = registry.DeepgramSTTConfiguration(api_key=["a", "b"], language="en")
    target = config.model_copy(update={"api_key": ["b", "a"], "language": "pt"})
    stt = SimpleNamespace(_settings=service_factory.DeepgramSTTSettings(language="en"))
    with patch.object(
        registry.random, "choice", side_effect=AssertionError("must not pick a key")
    ):
        delta = service_factory.get_stt_settings_update(stt, config, target)
    assert delta.language == "pt"


@pytest.mark.parametrize(
    "config_name,settings,target_settings",
    [
        ("GladiaSTTConfiguration", {"language": "en"}, {"language": "pt"}),
        ("CartesiaSTTConfiguration", {"model": "ink-2"}, {"language": "pt"}),
        ("DeepgramSTTConfiguration", {"model": "flux-general-en"}, {"language": "pt"}),
        ("DograhSTTService", {"language": "en"}, {"language": "ta"}),
        ("DograhSTTService", {"language": "ta"}, {"language": "en"}),
        ("DograhSTTService", {"language": "ta"}, {"language": "te"}),
        (
            "AssemblyAISTTConfiguration",
            {"model": "universal-streaming-multilingual"},
            {"language": "pt"},
        ),
        ("OpenAISTTConfiguration", {}, {}),
    ],
)
def test_unsupported_updates_do_not_mutate_service(
    config_name, settings, target_settings
):
    config = getattr(registry, config_name)(api_key="test-key", **settings)
    # An unsupported update must return before even inspecting live settings.
    assert (
        service_factory.get_stt_settings_update(
            object(), config, config.model_copy(update=target_settings)
        )
        is None
    )


@pytest.mark.asyncio
async def test_soniox_auto_detect_clears_previous_language_hints():
    config = registry.SonioxSTTConfiguration(api_key="test-key", language="en")
    stt = create_stt(config)
    stt._request_reconnect = AsyncMock()
    assert await retune(
        factory_for(stt, config), config.model_copy(update={"language": "multi"})
    )
    assert stt._settings.language_hints == []
    stt._request_reconnect.assert_awaited_once()
