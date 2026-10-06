"""Adapt old workflow model editors to organization-owned catalog references."""

from copy import deepcopy

from fastapi import HTTPException

from api.schemas.ai_model_configuration import OrganizationAIModelConfigurationV2
from api.schemas.model_configuration_migration import (
    MODEL_CONFIGURATION_DEFAULT_KEY,
    WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY,
)
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
    check_for_masked_keys_in_ai_model_configuration_v2,
    merge_ai_model_configuration_v2_secrets,
)
from api.services.configuration.merge import merge_workflow_configuration_secrets
from api.services.configuration.model_configuration_migration import (
    ensure_legacy_workflow_model_configuration,
)

_LEGACY_KEYS = (WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY, "model_overrides")


async def adapt_legacy_workflow_model_configuration(
    *,
    organization_id: int,
    workflow_id: int,
    workflow_configurations: dict | None,
    database,
) -> dict | None:
    """Merge stored secret masks and materialize legacy input for V3 organizations.

    Ownership is established before importing any connections. Unchanged legacy
    fields echoed by an old form do not shadow an existing V3 selection. New
    clients clear inheritance by removing all model keys, so absence is not
    treated as a patch that resurrects a deleted override.
    """
    if (
        not workflow_configurations
        or workflow_configurations.get(WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY)
        is not None
    ):
        return workflow_configurations
    if not any(workflow_configurations.get(key) for key in _LEGACY_KEYS):
        return workflow_configurations
    default = await database.get_configuration(
        organization_id, MODEL_CONFIGURATION_DEFAULT_KEY
    )
    if default is None or not default.value:
        return workflow_configurations
    workflow = await database.get_workflow(workflow_id, organization_id=organization_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    draft = await database.get_draft_version(workflow_id)
    definition = draft or workflow.released_definition
    existing = (
        definition.workflow_configurations
        if definition
        else workflow.workflow_configurations
    ) or {}
    incoming = deepcopy(workflow_configurations)
    try:
        if incoming.get(WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY):
            v2 = OrganizationAIModelConfigurationV2.model_validate(
                incoming[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY]
            )
            existing_v2_data = existing.get(
                WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY
            )
            if existing_v2_data:
                existing_v2 = OrganizationAIModelConfigurationV2.model_validate(
                    existing_v2_data
                )
            else:
                organization = await database.get_configuration(
                    organization_id, "MODEL_CONFIGURATION_V2"
                )
                existing_v2 = (
                    OrganizationAIModelConfigurationV2.model_validate(
                        organization.value
                    )
                    if organization and organization.value
                    else None
                )
            v2 = merge_ai_model_configuration_v2_secrets(v2, existing_v2)
            check_for_masked_keys_in_ai_model_configuration_v2(v2)
            incoming[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY] = v2.model_dump(
                mode="json", exclude_none=True
            )
            unchanged = bool(
                existing_v2_data
            ) and v2 == OrganizationAIModelConfigurationV2.model_validate(
                existing_v2_data
            )
        else:
            incoming = merge_workflow_configuration_secrets(incoming, existing)
            unchanged = incoming.get("model_overrides") == existing.get(
                "model_overrides"
            )
        if (
            unchanged
            and existing.get(WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY) is not None
        ):
            override = deepcopy(existing[WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY])
        else:
            specification = await ensure_legacy_workflow_model_configuration(
                organization_id, incoming
            )
            if specification is None:
                raise ValueError("Missing legacy model configuration")
            override = deepcopy(specification)
            override.pop("version", None)
        incoming[WORKFLOW_MODEL_CONFIGURATION_OVERRIDE_KEY] = override
        for key in _LEGACY_KEYS:
            incoming.pop(key, None)
        return incoming
    except ValueError:
        # Pydantic and legacy resolver errors can include submitted credentials.
        raise HTTPException(
            status_code=422,
            detail="Invalid workflow model configuration. Review its model settings and credentials.",
        ) from None
