"""Service for duplicating workflows."""

import copy
import posixpath
import uuid

from loguru import logger

from api.db import db_client
from api.enums import StorageBackend
from api.services.storage import get_storage_for_backend, storage_fs


def _extract_trigger_paths(workflow_definition: dict) -> list[str]:
    """Extract trigger UUIDs from workflow definition."""
    if not workflow_definition:
        return []
    nodes = workflow_definition.get("nodes", [])
    trigger_paths = []
    for node in nodes:
        if node.get("type") == "trigger":
            trigger_path = node.get("data", {}).get("trigger_path")
            if trigger_path:
                trigger_paths.append(trigger_path)
    return trigger_paths


def _regenerate_trigger_uuids(workflow_definition: dict) -> dict:
    """Regenerate UUIDs for all trigger nodes to avoid conflicts."""
    if not workflow_definition:
        return workflow_definition
    updated_definition = copy.deepcopy(workflow_definition)
    nodes = updated_definition.get("nodes", [])
    for node in nodes:
        if node.get("type") == "trigger":
            if "data" not in node:
                node["data"] = {}
            node["data"]["trigger_path"] = str(uuid.uuid4())
    return updated_definition


async def duplicate_workflow(
    workflow_id: int,
    organization_id: int,
    user_id: int,
):
    """Duplicate a workflow including its definition, config, and triggers.

    Recordings are org-scoped and shared, so they are not duplicated.

    Args:
        workflow_id: The source workflow ID to duplicate
        organization_id: The organization ID
        user_id: The user performing the duplication

    Returns:
        The newly created workflow DB object

    Raises:
        ValueError: If the source workflow is not found
    """
    # 1. Fetch source workflow
    source = await db_client.get_workflow(workflow_id, organization_id=organization_id)
    if source is None:
        raise ValueError(f"Workflow with id {workflow_id} not found")

    # 2. Prefer draft over released definition (duplicate latest state)
    draft = await db_client.get_draft_version(workflow_id)
    source_def = draft if draft else source.released_definition

    workflow_definition = copy.deepcopy(source_def.workflow_json)

    # 3. Regenerate trigger UUIDs to avoid conflicts
    if workflow_definition:
        workflow_definition = _regenerate_trigger_uuids(workflow_definition)

    # 4. Create the new workflow
    new_name = f"{source.name} - Duplicate"
    new_workflow = await db_client.create_workflow(
        name=new_name,
        workflow_definition=workflow_definition,
        user_id=user_id,
        organization_id=organization_id,
    )

    # 5. Copy template_context_variables and workflow_configurations from source definition
    source_tcv = source_def.template_context_variables
    source_wc = (
        copy.deepcopy(source_def.workflow_configurations)
        if source_def.workflow_configurations
        else None
    )

    # 5a. Copy custom ambient noise file if present
    if source_wc:
        ambient_cfg = source_wc.get("ambient_noise_configuration")
        if ambient_cfg and ambient_cfg.get("storage_key"):
            old_key = ambient_cfg["storage_key"]
            filename = posixpath.basename(old_key)
            new_key = f"ambient-noise/{organization_id}/{new_workflow.id}/{filename}"
            try:
                if await _copy_storage_object(
                    old_key, new_key, ambient_cfg.get("storage_backend", "")
                ):
                    ambient_cfg["storage_key"] = new_key
                else:
                    logger.warning(
                        f"Failed to copy ambient noise file {old_key}, keeping original reference"
                    )
            except Exception as e:
                logger.error(f"Error copying ambient noise file: {e}")

    if source_tcv or source_wc:
        new_workflow = await db_client.update_workflow(
            workflow_id=new_workflow.id,
            name=None,
            workflow_definition=None,
            template_context_variables=copy.deepcopy(source_tcv),
            workflow_configurations=source_wc,
            organization_id=organization_id,
        )

    # 6. Sync triggers for the new workflow
    if workflow_definition:
        trigger_paths = _extract_trigger_paths(workflow_definition)
        if trigger_paths:
            await db_client.sync_triggers_for_workflow(
                workflow_id=new_workflow.id,
                organization_id=organization_id,
                trigger_paths=trigger_paths,
            )

    return new_workflow


async def _copy_storage_object(
    source_key: str, dest_key: str, storage_backend: str
) -> bool:
    """Copy a file in storage, resolving the correct backend. Returns True on success."""
    current_backend = StorageBackend.get_current_backend()
    fs = (
        storage_fs
        if storage_backend == current_backend.value
        else get_storage_for_backend(storage_backend)
    )
    return await fs.acopy_file(source_key, dest_key)
