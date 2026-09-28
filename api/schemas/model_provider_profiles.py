"""Schemas for named, saved model provider profiles.

A profile is a named provider configuration (credentials + default settings)
for one service (llm, tts, stt or realtime). An organization may save several
profiles, including several for the same provider (for example two OpenAI
accounts, or an EU and a US ElevenLabs endpoint). API callers reference a
profile by name instead of sending credentials.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.services.configuration.registry import (
    REGISTRY,
    BaseServiceConfiguration,
    ServiceProviders,
    ServiceType,
)

ProfileService = Literal["llm", "tts", "stt", "realtime"]
PROFILE_SERVICES: tuple[str, ...] = ("llm", "tts", "stt", "realtime")

PROFILE_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,47}$"
_PROFILE_NAME_RE = re.compile(PROFILE_NAME_PATTERN)


def validate_profile_name(name: str) -> str:
    if not isinstance(name, str) or not _PROFILE_NAME_RE.fullmatch(name):
        raise ValueError(
            "Profile name must be 1-48 characters: lowercase letters, digits, "
            "'-' or '_', starting with a letter or digit"
        )
    return name


def parse_profile_config(
    service: str, config: dict[str, Any]
) -> BaseServiceConfiguration:
    """Validate ``config`` against the registry class for ``service``/provider.

    The managed ``dograh`` provider is not allowed in profiles: it is selected
    through the organization's default configuration.
    """
    if service not in PROFILE_SERVICES:
        raise ValueError(f"Unsupported service '{service}'")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")

    provider = config.get("provider")
    if not provider:
        raise ValueError("config.provider is required")
    if not isinstance(provider, str):
        raise ValueError("config.provider must be a string")
    if provider == ServiceProviders.DOGRAH.value:
        raise ValueError(
            "The managed 'dograh' provider cannot be saved as a profile; "
            "use the default configuration instead"
        )

    config_cls = REGISTRY.get(ServiceType[service.upper()], {}).get(provider)
    if config_cls is None:
        raise ValueError(f"Unknown {service} provider '{provider}'")
    return config_cls(**config)


class ModelProviderProfile(BaseModel):
    name: str
    service: ProfileService
    config: dict[str, Any]

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return validate_profile_name(value)

    @model_validator(mode="after")
    def _check_config(self):
        # Normalise through the registry class so stored configs always carry
        # every field with its default filled in.
        parsed = parse_profile_config(self.service, self.config)
        self.config = parsed.model_dump(mode="json", exclude_none=True)
        return self


class ModelProviderProfilesDocument(BaseModel):
    """The JSON document stored under ``MODEL_PROVIDER_PROFILES``."""

    version: Literal[1] = 1
    profiles: list[ModelProviderProfile] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique(self):
        seen: set[tuple[str, str]] = set()
        for profile in self.profiles:
            key = (profile.service, profile.name)
            if key in seen:
                raise ValueError(
                    f"Duplicate {profile.service} profile name '{profile.name}'"
                )
            seen.add(key)
        return self


class ModelProviderProfileCreateRequest(BaseModel):
    name: str
    service: ProfileService
    config: dict[str, Any]


class ModelProviderProfileUpdateRequest(BaseModel):
    config: dict[str, Any]


class ModelProviderProfileResponse(BaseModel):
    """A profile as returned to clients: secrets are masked."""

    name: str
    service: ProfileService
    config: dict[str, Any]


class ModelProviderProfilesResponse(BaseModel):
    profiles: list[ModelProviderProfileResponse]


class CallServiceOverride(BaseModel):
    """Per-call override for one service: a saved profile plus field tweaks.

    ``profile`` names a saved provider profile. Any additional keys override
    that profile's non-secret settings for this call only (for example
    ``model`` or ``voice``). Credentials, ``provider`` and endpoint URLs are
    not accepted here: create a separate profile for those.
    """

    model_config = ConfigDict(extra="allow")

    profile: str

    @field_validator("profile")
    @classmethod
    def _check_profile(cls, value: str) -> str:
        return validate_profile_name(value)


class CallModelOverrides(BaseModel):
    """Model overrides for a single call, selecting saved provider profiles."""

    model_config = ConfigDict(extra="forbid")

    llm: CallServiceOverride | None = None
    tts: CallServiceOverride | None = None
    stt: CallServiceOverride | None = None
    realtime: CallServiceOverride | None = None
    # Force speech-to-speech on or off. Defaults to on when ``realtime`` is set.
    is_realtime: bool | None = None
