"""A service registration owns account identity, labels and legacy routing."""

from typing import Literal

import pytest

from api.services.configuration import model_connections as connections
from api.services.configuration.registry import (
    REGISTRY,
    BaseLLMConfiguration,
    Provider,
    ServiceType,
    get_provider_definition,
    get_service_configuration,
    register_llm,
    register_service,
)


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    for role, configurations in list(REGISTRY.items()):
        monkeypatch.setitem(REGISTRY, role, dict(configurations))


FUTURE = Provider(
    id="future",
    title="Future AI",
    description="A provider registered after the catalog consumer was imported.",
    provider_docs_url="https://example.com/docs",
)


class FutureChat(BaseLLMConfiguration):
    provider: Literal["future_chat_v1"] = "future_chat_v1"
    model: str = "chat-model"


class FutureVoice(BaseLLMConfiguration):
    # Deliberately has no naming relationship to the account or realtime role.
    provider: Literal["voice_protocol_v2"] = "voice_protocol_v2"
    model: str = "voice-model"


def test_new_service_registration_drives_catalog_and_resolution_without_alias_edits():
    register_llm(provider=FUTURE)(FutureChat)
    register_service(ServiceType.REALTIME, provider=FUTURE)(FutureVoice)

    catalog = connections.model_connection_catalog()["services"]
    for role, model in (("llm", "chat-model"), ("realtime", "voice-model")):
        entry = catalog[role]["future"]
        assert entry["title"] == "Future AI"
        assert entry["settings_schema"]["properties"]["model"]["default"] == model
        assert "future_chat_v1" not in catalog[role]
        assert "voice_protocol_v2" not in catalog[role]

    for account in ("future", "future_chat_v1", "voice_protocol_v2"):
        assert connections.connection_provider(account) == "future"
        assert get_provider_definition(account) == FUTURE
        assert get_service_configuration(ServiceType.LLM, account) is FutureChat
        config = connections._build_service(
            "realtime", account, {"api_key": "key"}, {}, {}
        )
        assert isinstance(config, FutureVoice)
        assert config.provider == "voice_protocol_v2"
        assert config.model == "voice-model"
        connections.validate_provider_connection(account, {"api_key": "key"}, {})

    schema = FutureVoice.model_json_schema()
    assert schema["title"] == FUTURE.title
    assert schema["description"] == FUTURE.description
    assert schema["provider_docs_url"] == FUTURE.provider_docs_url
    assert "provider_definition" not in schema["properties"]
    assert get_service_configuration(ServiceType.STT, "future") is None


def test_registration_requires_provider_metadata_and_a_runtime_default():
    with pytest.raises(TypeError):
        register_service(ServiceType.LLM)
    with pytest.raises(ValueError, match="Runtime provider default required"):
        register_llm(provider=FUTURE)(BaseLLMConfiguration)
    assert get_provider_definition("future") is None


def test_duplicate_account_role_cannot_silently_replace_a_service():
    register_llm(provider=FUTURE)(FutureChat)
    with pytest.raises(ValueError, match="Duplicate LLM provider future"):
        register_llm(provider=FUTURE)(FutureVoice)
    assert get_service_configuration(ServiceType.LLM, "future") is FutureChat
    assert "voice_protocol_v2" not in REGISTRY[ServiceType.LLM]


def test_shared_provider_metadata_cannot_disagree():
    register_llm(provider=FUTURE)(FutureChat)
    with pytest.raises(ValueError, match="Conflicting metadata"):
        register_service(
            ServiceType.REALTIME, provider=Provider(id="future", title="Different name")
        )(FutureVoice)


@pytest.mark.parametrize("collision", ["runtime", "account", "legacy_alias"])
def test_ambiguous_account_and_runtime_ids_are_rejected(collision):
    register_llm(provider=FUTURE)(FutureChat)
    runtime = {
        "runtime": "future_chat_v1",
        "account": "future",
        "legacy_alias": "other_runtime",
    }[collision]
    provider = Provider(
        id="future_chat_v1" if collision == "legacy_alias" else "other_account",
        title="Other account",
    )

    class ConflictingVoice(BaseLLMConfiguration):
        provider: str = runtime
        model: str = "voice-model"

    with pytest.raises(ValueError, match="Ambiguous provider identity"):
        register_service(ServiceType.REALTIME, provider=provider)(ConflictingVoice)
    assert get_provider_definition("future_chat_v1") == FUTURE


@pytest.mark.parametrize("provider_id,title", [("", "Name"), ("provider", " ")])
def test_provider_requires_a_display_name_and_identity(provider_id, title):
    with pytest.raises(ValueError, match="Provider ID and title are required"):
        Provider(id=provider_id, title=title)
