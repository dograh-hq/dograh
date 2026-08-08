from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from api.services.configuration.registry import (
    DograhEmbeddingsConfiguration,
    DograhLLMService,
    DograhSTTService,
    DograhTTSService,
    EmbeddingsConfig,
    LLMConfig,
    RealtimeConfig,
    ServiceProviders,
    STTConfig,
    TTSConfig,
)

# Service segments that can be individually overridden
ServiceSegment = Literal["llm", "tts", "stt", "embeddings", "realtime"]

DOGRAH_SPEED_MIN = 0.5
DOGRAH_SPEED_MAX = 2.0
DOGRAH_SPEED_STEP = 0.1
DOGRAH_SPEED_OPTIONS: tuple[float, ...] = (0.8, 1.0, 1.2)
DOGRAH_DEFAULT_VOICE = "default"
DOGRAH_DEFAULT_LANGUAGE = "multi"


class EffectiveAIModelConfiguration(BaseModel):
    llm: LLMConfig | None = None
    stt: STTConfig | None = None
    tts: TTSConfig | None = None
    embeddings: EmbeddingsConfig | None = None
    realtime: RealtimeConfig | None = None
    is_realtime: bool = False
    managed_service_version: int | None = None
    test_phone_number: str | None = None
    timezone: str | None = None
    last_validated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def strip_incomplete_realtime_when_disabled(cls, data):
        """Skip realtime validation when is_realtime is False and api_key is missing."""
        if isinstance(data, dict) and not data.get("is_realtime", False):
            realtime = data.get("realtime")
            if isinstance(realtime, dict) and not realtime.get("api_key"):
                data.pop("realtime", None)
        return data


class DograhManagedAIModelConfiguration(BaseModel):
    api_key: str
    voice: str = DOGRAH_DEFAULT_VOICE
    speed: float = Field(default=1.0, ge=DOGRAH_SPEED_MIN, le=DOGRAH_SPEED_MAX)
    language: str = DOGRAH_DEFAULT_LANGUAGE


class BYOKPipelineAIModelConfiguration(BaseModel):
    llm: LLMConfig | None = None
    tts: TTSConfig | None = None
    stt: STTConfig | None = None
    embeddings: EmbeddingsConfig | None = None

    @model_validator(mode="after")
    def reject_dograh_providers(self):
        _reject_dograh_provider("llm", self.llm)
        _reject_dograh_provider("tts", self.tts)
        _reject_dograh_provider("stt", self.stt)
        _reject_dograh_provider("embeddings", self.embeddings)
        return self

    def has_any_service(self) -> bool:
        """Check if at least one service is configured."""
        return self.llm is not None or self.tts is not None or self.stt is not None or self.embeddings is not None


class BYOKPipelineAIModelConfigurationRequired(BaseModel):
    """Full BYOK pipeline config with all required fields (for org-level config)."""
    llm: LLMConfig
    tts: TTSConfig
    stt: STTConfig
    embeddings: EmbeddingsConfig | None = None

    @model_validator(mode="after")
    def reject_dograh_providers(self):
        _reject_dograh_provider("llm", self.llm)
        _reject_dograh_provider("tts", self.tts)
        _reject_dograh_provider("stt", self.stt)
        _reject_dograh_provider("embeddings", self.embeddings)
        return self


class BYOKRealtimeAIModelConfiguration(BaseModel):
    realtime: RealtimeConfig | None = None
    llm: LLMConfig | None = None
    embeddings: EmbeddingsConfig | None = None

    @model_validator(mode="after")
    def reject_dograh_providers(self):
        _reject_dograh_provider("llm", self.llm)
        _reject_dograh_provider("embeddings", self.embeddings)
        return self

    def has_any_service(self) -> bool:
        """Check if at least one service is configured."""
        return self.realtime is not None or self.llm is not None or self.embeddings is not None


class BYOKRealtimeAIModelConfigurationRequired(BaseModel):
    """Full BYOK realtime config with all required fields (for org-level config)."""
    realtime: RealtimeConfig
    llm: LLMConfig
    embeddings: EmbeddingsConfig | None = None

    @model_validator(mode="after")
    def reject_dograh_providers(self):
        _reject_dograh_provider("llm", self.llm)
        _reject_dograh_provider("embeddings", self.embeddings)
        return self


class BYOKAIModelConfiguration(BaseModel):
    mode: Literal["pipeline", "realtime"]
    pipeline: BYOKPipelineAIModelConfiguration | None = None
    realtime: BYOKRealtimeAIModelConfiguration | None = None

    @model_validator(mode="after")
    def validate_selected_mode(self):
        if self.mode == "pipeline" and self.pipeline is None:
            raise ValueError("byok.pipeline is required when byok.mode is pipeline")
        if self.mode == "realtime" and self.realtime is None:
            raise ValueError("byok.realtime is required when byok.mode is realtime")
        return self

    def has_any_service(self) -> bool:
        """Check if at least one service is configured."""
        if self.mode == "pipeline" and self.pipeline:
            return self.pipeline.has_any_service()
        if self.mode == "realtime" and self.realtime:
            return self.realtime.has_any_service()
        return False


class OrganizationAIModelConfigurationV2(BaseModel):
    version: Literal[2] = 2
    mode: Literal["dograh", "byok"]
    dograh: DograhManagedAIModelConfiguration | None = None
    byok: BYOKAIModelConfiguration | None = None
    # When set, only these services are overridden; rest inherit from org config
    overridden_services: list[ServiceSegment] | None = None

    @model_validator(mode="after")
    def validate_selected_mode(self):
        if self.mode == "dograh" and self.dograh is None:
            raise ValueError("dograh configuration is required when mode is dograh")
        if self.mode == "byok" and self.byok is None:
            raise ValueError("byok configuration is required when mode is byok")
        return self

    @model_validator(mode="after")
    def validate_overridden_services(self):
        if self.overridden_services:
            # Validate that overridden_services only contains services present in byok config
            if self.mode == "byok" and self.byok:
                if self.byok.mode == "pipeline" and self.byok.pipeline:
                    available = set()
                    if self.byok.pipeline.llm:
                        available.add("llm")
                    if self.byok.pipeline.tts:
                        available.add("tts")
                    if self.byok.pipeline.stt:
                        available.add("stt")
                    if self.byok.pipeline.embeddings:
                        available.add("embeddings")
                    invalid = set(self.overridden_services) - available
                    if invalid:
                        raise ValueError(
                            f"overridden_services {invalid} not present in byok pipeline config"
                        )
                elif self.byok.mode == "realtime" and self.byok.realtime:
                    available = set()
                    if self.byok.realtime.realtime:
                        available.add("realtime")
                    if self.byok.realtime.llm:
                        available.add("llm")
                    if self.byok.realtime.embeddings:
                        available.add("embeddings")
                    invalid = set(self.overridden_services) - available
                    if invalid:
                        raise ValueError(
                            f"overridden_services {invalid} not present in byok realtime config"
                        )
        return self

    def is_partial_override(self) -> bool:
        """Check if this is a partial override (only some services)."""
        return bool(self.overridden_services)


class OrganizationAIModelConfigurationResponse(BaseModel):
    configuration: dict | None
    effective_configuration: dict
    source: Literal["organization_v2", "legacy_user_v1", "empty"]


def compile_ai_model_configuration_v2(
    configuration: OrganizationAIModelConfigurationV2,
    org_config: OrganizationAIModelConfigurationV2 | None = None,
) -> EffectiveAIModelConfiguration:
    """Compile a v2 configuration into an effective configuration.
    
    If configuration has overridden_services and org_config is provided,
    only the specified services are taken from configuration; the rest
    are inherited from org_config.
    """
    # If no partial override or no org_config to merge with, use full compilation
    if not configuration.is_partial_override() or org_config is None:
        return _compile_full(configuration)

    # First, compile the org config to get the base effective config
    org_effective = _compile_full(org_config)
    
    # Build the override effective config (only the overridden services)
    override_effective = _compile_full(configuration)
    
    # Now merge: start with org_effective, then overlay only the overridden services
    merged_dict = org_effective.model_dump()
    
    if configuration.overridden_services:
        for svc in configuration.overridden_services:
            if svc == "llm" and override_effective.llm:
                merged_dict["llm"] = override_effective.llm
            elif svc == "tts" and override_effective.tts:
                merged_dict["tts"] = override_effective.tts
            elif svc == "stt" and override_effective.stt:
                merged_dict["stt"] = override_effective.stt
            elif svc == "embeddings" and override_effective.embeddings:
                merged_dict["embeddings"] = override_effective.embeddings
            elif svc == "realtime" and override_effective.realtime:
                merged_dict["realtime"] = override_effective.realtime
    
    return EffectiveAIModelConfiguration.model_validate(merged_dict)


def _compile_full(configuration: OrganizationAIModelConfigurationV2) -> EffectiveAIModelConfiguration:
    """Full compilation without partial override merging."""
    if configuration.mode == "dograh":
        if configuration.dograh is None:
            raise ValueError("dograh configuration is required")
        return _compile_dograh_configuration(configuration.dograh)

    if configuration.byok is None:
        raise ValueError("byok configuration is required")
    if configuration.byok.mode == "pipeline":
        if configuration.byok.pipeline is None:
            raise ValueError("byok.pipeline is required")
        pipeline = configuration.byok.pipeline
        if not pipeline.has_any_service():
            raise ValueError("byok.pipeline must have at least one service configured")
        return EffectiveAIModelConfiguration(
            llm=pipeline.llm,
            tts=pipeline.tts,
            stt=pipeline.stt,
            embeddings=pipeline.embeddings,
            is_realtime=False,
        )

    if configuration.byok.realtime is None:
        raise ValueError("byok.realtime is required")
    realtime = configuration.byok.realtime
    if not realtime.has_any_service():
        raise ValueError("byok.realtime must have at least one service configured")
    return EffectiveAIModelConfiguration(
        llm=realtime.llm,
        realtime=realtime.realtime,
        embeddings=realtime.embeddings,
        is_realtime=True,
    )


def _compile_dograh_configuration(
    configuration: DograhManagedAIModelConfiguration,
) -> EffectiveAIModelConfiguration:
    return EffectiveAIModelConfiguration(
        llm=DograhLLMService(
            provider=ServiceProviders.DOGRAH,
            api_key=configuration.api_key,
            model="default",
        ),
        tts=DograhTTSService(
            provider=ServiceProviders.DOGRAH,
            api_key=configuration.api_key,
            model="default",
            voice=configuration.voice,
            speed=configuration.speed,
        ),
        stt=DograhSTTService(
            provider=ServiceProviders.DOGRAH,
            api_key=configuration.api_key,
            model="default",
            language=configuration.language,
        ),
        embeddings=DograhEmbeddingsConfiguration(
            provider=ServiceProviders.DOGRAH,
            api_key=configuration.api_key,
            model="dograh_embedding_v1",
        ),
        is_realtime=False,
        managed_service_version=2,
    )


def _reject_dograh_provider(section: str, service) -> None:
    if service is None:
        return
    if getattr(service, "provider", None) == ServiceProviders.DOGRAH:
        raise ValueError(f"BYOK {section} cannot use Dograh provider")
