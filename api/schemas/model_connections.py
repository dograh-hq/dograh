"""Secret-free reusable model configuration and override contracts."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CREDENTIAL_FIELDS = frozenset(
    {"api_key", "credentials", "aws_access_key", "aws_secret_key", "aws_session_token"}
)
CONNECTION_FIELDS = frozenset(
    {
        "base_url",
        "endpoint",
        "project_id",
        "location",
        "aws_region",
        "region",
        "api_version",
        "group_id",
        "bill_to",
    }
)


class CatalogSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ServiceSettings(CatalogSchema):
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("settings")
    @classmethod
    def reject_connection_fields(cls, settings):
        if settings.keys() & (CREDENTIAL_FIELDS | CONNECTION_FIELDS | {"provider"}):
            raise ValueError(
                "Settings cannot contain credentials, provider identity, or connection settings"
            )
        return settings


class ServiceSelection(ServiceSettings):
    provider_connection_uuid: UUID


class ServiceOverride(ServiceSettings):
    provider_connection_uuid: UUID | None = None

    @model_validator(mode="after")
    def reject_null_reference(self):
        if (
            "provider_connection_uuid" in self.model_fields_set
            and self.provider_connection_uuid is None
        ):
            raise ValueError("provider_connection_uuid cannot be null")
        return self


class ModelConfigurationSpec(CatalogSchema):
    version: Literal[3] = 3
    mode: Literal["pipeline", "realtime"] = "pipeline"
    llm: ServiceSelection
    stt: ServiceSelection | None = None
    tts: ServiceSelection | None = None
    realtime: ServiceSelection | None = None
    embeddings: ServiceSelection | None = None

    @model_validator(mode="after")
    def required_services(self):
        required = ("stt", "tts") if self.mode == "pipeline" else ("realtime",)
        if any(getattr(self, role) is None for role in required):
            raise ValueError(f"{self.mode} requires {', '.join(required)}")
        return self


class ModelConfigurationOverride(CatalogSchema):
    model_configuration_uuid: UUID | None = None
    mode: Literal["pipeline", "realtime"] | None = None
    llm: ServiceOverride | None = None
    stt: ServiceOverride | None = None
    tts: ServiceOverride | None = None
    realtime: ServiceOverride | None = None
    embeddings: ServiceOverride | None = None

    @model_validator(mode="after")
    def reject_ambiguous_nulls(self):
        for field in self.model_fields_set - {"embeddings"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ProviderConnectionCreate(CatalogSchema):
    name: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    credentials: dict[str, Any] = Field(default_factory=dict, repr=False)
    connection_settings: dict[str, Any] = Field(default_factory=dict)


class ProviderConnectionUpdate(CatalogSchema):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    credentials: dict[str, Any] | None = Field(default=None, repr=False)
    connection_settings: dict[str, Any] | None = None
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def reject_null_updates(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Omit unchanged fields instead of supplying null")
        return self


class ProviderConnectionResponse(CatalogSchema):
    uuid: UUID
    name: str
    provider: str
    connection_settings: dict[str, Any]
    configured_credentials: list[str]
    revision: int
    is_active: bool


class NamedModelConfigurationCreate(CatalogSchema):
    name: str = Field(min_length=1, max_length=128)
    configuration: ModelConfigurationSpec


class NamedModelConfigurationUpdate(CatalogSchema):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    configuration: ModelConfigurationSpec | None = None
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def reject_null_updates(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Omit unchanged fields instead of supplying null")
        return self


class NamedModelConfigurationResponse(CatalogSchema):
    uuid: UUID
    name: str
    configuration: ModelConfigurationSpec
    revision: int
    is_active: bool


class DefaultModelConfigurationRequest(CatalogSchema):
    model_configuration_uuid: UUID


class DefaultModelConfigurationResponse(CatalogSchema):
    model_configuration_uuid: UUID | None


class ModelConfigurationPreview(CatalogSchema):
    configuration: dict[str, Any]
    provenance: list[dict[str, Any]]
