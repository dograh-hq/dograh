"""Read an organization's effective model configuration from the catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException

from api.constants import MPS_API_URL
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.registry import ServiceProviders

AIModelConfigurationSource = Literal["organization_v3", "empty"]
# Retired inline workflow key. Rows converted by the catalog backfill keep it
# as audit data; no code reads it, and saves drop it.
WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY = "model_configuration_v2_override"


@dataclass
class ResolvedAIModelConfiguration:
    effective: EffectiveAIModelConfiguration
    source: AIModelConfigurationSource


async def get_resolved_ai_model_configuration(
    *,
    organization_id: int | None,
) -> ResolvedAIModelConfiguration:
    """Resolve the organization's default configuration, or an empty one."""
    if organization_id is not None:
        from api.services.configuration.model_connections import (
            get_default_model_configuration,
            resolve_model_configuration,
        )

        default = await get_default_model_configuration(organization_id)
        if default is not None:
            resolved = await resolve_model_configuration(
                organization_id, default_row=default
            )
            return ResolvedAIModelConfiguration(
                effective=resolved.effective, source="organization_v3"
            )
    return ResolvedAIModelConfiguration(
        effective=EffectiveAIModelConfiguration(), source="empty"
    )


async def get_effective_ai_model_configuration_for_workflow(
    *,
    organization_id: int | None,
    workflow_configurations: dict | None,
) -> EffectiveAIModelConfiguration:
    """The definition's catalog binding layered on the organization default."""
    workflow_configurations = workflow_configurations or {}
    if workflow_configurations.get("model_configuration_override") is not None:
        from api.services.configuration.model_connections import (
            resolve_model_configuration,
        )

        if organization_id is None:
            raise HTTPException(
                status_code=400,
                detail="Organization is required for model configuration.",
            )
        resolved = await resolve_model_configuration(
            organization_id,
            workflow_override=workflow_configurations["model_configuration_override"],
        )
        return resolved.effective
    resolved_config = await get_resolved_ai_model_configuration(
        organization_id=organization_id,
    )
    return resolved_config.effective


def dograh_embeddings_base_url() -> str:
    # AsyncOpenAI appends "/embeddings"; MPS exposes that under /api/v1/llm.
    return f"{MPS_API_URL}/api/v1/llm"


def apply_managed_embeddings_base_url(
    *,
    provider: str | None,
    base_url: str | None,
) -> str | None:
    if provider == ServiceProviders.DOGRAH.value or provider == ServiceProviders.DOGRAH:
        return dograh_embeddings_base_url()
    return base_url
