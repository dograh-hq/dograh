"""Service for duplicating workflows including recordings."""

import copy
import json
import posixpath
import uuid

from loguru import logger

from api.db import db_client
from api.db.workflow_recording_client import generate_short_id
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


async def _generate_unique_recording_id() -> str:
    """Generate a globally unique short recording ID."""
    for _ in range(10):
        rid = generate_short_id(8)
        exists = await db_client.check_recording_id_exists(rid)
        if not exists:
            return rid
    raise RuntimeError("Failed to generate unique recording ID")


async def duplicate_workflow(
    workflow_id: int,
    organization_id: int,
    user_id: int,
):
    """Duplicate a workflow including its definition, config, recordings, and triggers.

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
    source_wc = source_def.workflow_configurations
    if source_tcv or source_wc:
        new_workflow = await db_client.update_workflow(
            workflow_id=new_workflow.id,
            name=None,
            workflow_definition=None,
            template_context_variables=copy.deepcopy(source_tcv),
            workflow_configurations=copy.deepcopy(source_wc),
            organization_id=organization_id,
        )

    # 6. Copy recordings with new IDs and storage paths scoped to new workflow
    recording_id_map = await _duplicate_recordings(
        source_workflow_id=workflow_id,
        new_workflow_id=new_workflow.id,
        organization_id=organization_id,
        user_id=user_id,
    )

    # 7. Replace old recording IDs with new ones in the workflow definition
    if recording_id_map:
        workflow_definition = _replace_recording_ids(
            workflow_definition, recording_id_map
        )
        new_workflow = await db_client.update_workflow(
            workflow_id=new_workflow.id,
            name=None,
            workflow_definition=workflow_definition,
            template_context_variables=None,
            workflow_configurations=None,
            organization_id=organization_id,
        )

    # 8. Sync triggers for the new workflow
    if workflow_definition:
        trigger_paths = _extract_trigger_paths(workflow_definition)
        if trigger_paths:
            await db_client.sync_triggers_for_workflow(
                workflow_id=new_workflow.id,
                organization_id=organization_id,
                trigger_paths=trigger_paths,
            )

    return new_workflow


async def _duplicate_recordings(
    source_workflow_id: int,
    new_workflow_id: int,
    organization_id: int,
    user_id: int,
) -> dict[str, str]:
    """Duplicate all recordings for a workflow.

    Copies each recording file to a new storage path scoped under the new
    workflow ID, and creates new DB records pointing to the copied files.

    Returns:
        Mapping of old_recording_id -> new_recording_id
    """
    recordings = await db_client.get_recordings_for_workflow(
        workflow_id=source_workflow_id,
        organization_id=organization_id,
    )

    if not recordings:
        return {}

    recording_id_map: dict[str, str] = {}

    for rec in recordings:
        try:
            new_recording_id = await _generate_unique_recording_id()

            # Build new storage key: recordings/{org_id}/{new_workflow_id}/{new_recording_id}/{filename}
            filename = posixpath.basename(rec.storage_key)
            new_storage_key = (
                f"recordings/{organization_id}"
                f"/{new_workflow_id}/{new_recording_id}"
                f"/{filename}"
            )

            # Copy the file in storage (server-side copy)
            fs = _get_storage_for_recording(rec.storage_backend)
            copied = await fs.acopy_file(rec.storage_key, new_storage_key)
            if not copied:
                logger.warning(
                    f"Failed to copy recording file {rec.recording_id}, skipping"
                )
                continue

            await db_client.create_recording(
                recording_id=new_recording_id,
                workflow_id=new_workflow_id,
                organization_id=organization_id,
                tts_provider=rec.tts_provider,
                tts_model=rec.tts_model,
                tts_voice_id=rec.tts_voice_id,
                transcript=rec.transcript,
                storage_key=new_storage_key,
                storage_backend=rec.storage_backend,
                created_by=user_id,
                metadata=copy.deepcopy(rec.recording_metadata),
            )

            recording_id_map[rec.recording_id] = new_recording_id
            logger.info(
                f"Duplicated recording {rec.recording_id} -> {new_recording_id}"
            )

        except Exception as e:
            logger.error(f"Error duplicating recording {rec.recording_id}: {e}")
            continue

    return recording_id_map


def _replace_recording_ids(
    workflow_definition: dict,
    recording_id_map: dict[str, str],
) -> dict:
    """Replace old recording IDs with new ones throughout the workflow definition.

    Uses JSON serialization to do a thorough find-and-replace across all
    nested fields (node prompts, data, etc.).
    """
    definition_str = json.dumps(workflow_definition)

    for old_id, new_id in recording_id_map.items():
        definition_str = definition_str.replace(old_id, new_id)

    return json.loads(definition_str)


def _get_storage_for_recording(storage_backend: str):
    """Get the appropriate storage filesystem for a recording's backend."""
    current_backend = StorageBackend.get_current_backend()
    if storage_backend == current_backend.value:
        return storage_fs
    return get_storage_for_backend(storage_backend)
