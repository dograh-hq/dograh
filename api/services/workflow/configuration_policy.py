"""Workflow-configuration policies shared by workflow update surfaces."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from api.db import db_client
from api.services.organization_preferences import external_pbx_integrations_enabled


class WorkflowConfigurationNotFoundError(LookupError):
    """Raised when configuration policy cannot resolve the target workflow."""


class ExternalPBXConfigurationDisabledError(PermissionError):
    """Raised when a disabled organization changes external-PBX mappings."""


async def apply_external_pbx_mapping_policy(
    workflow_configurations: dict[str, Any] | None,
    *,
    workflow_id: int,
    organization_id: int,
) -> dict[str, Any] | None:
    """Preserve hidden mappings and reject edits while external PBX is disabled.

    Workflow configuration updates replace the stored configuration document. When
    the External PBX UI is hidden, its field mappings are absent from the request,
    so they must be copied from the active draft or published definition before the
    update is persisted.
    """
    if workflow_configurations is None or await external_pbx_integrations_enabled(
        organization_id
    ):
        return workflow_configurations

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise WorkflowConfigurationNotFoundError(
            f"Workflow with id {workflow_id} not found"
        )

    draft = await db_client.get_draft_version(workflow_id)
    stored_configurations = (
        draft.workflow_configurations
        if draft
        else workflow.released_definition.workflow_configurations
    )
    stored_mappings = (stored_configurations or {}).get(
        "external_pbx_field_mappings", []
    )
    incoming_mappings = workflow_configurations.get(
        "external_pbx_field_mappings", stored_mappings
    )

    if incoming_mappings != stored_mappings:
        raise ExternalPBXConfigurationDisabledError(
            "External PBX integrations are disabled for this organization. "
            "Enable them in Platform Settings before changing field mappings."
        )

    if not stored_mappings:
        return workflow_configurations

    prepared_configurations = dict(workflow_configurations)
    prepared_configurations["external_pbx_field_mappings"] = deepcopy(stored_mappings)
    return prepared_configurations
