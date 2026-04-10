"""Database client for managing workflow recordings."""

import secrets
import string
from typing import List, Optional

from loguru import logger
from sqlalchemy import func, select, text

from api.db.base_client import BaseDBClient
from api.db.models import WorkflowRecordingModel


def generate_short_id(length: int = 8) -> str:
    """Generate a random lowercase alphanumeric short ID."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class WorkflowRecordingClient(BaseDBClient):
    """Client for managing workflow audio recordings."""

    async def create_recording(
        self,
        recording_id: str,
        organization_id: int,
        transcript: str,
        storage_key: str,
        storage_backend: str,
        created_by: int,
        workflow_id: Optional[int] = None,
        tts_provider: Optional[str] = None,
        tts_model: Optional[str] = None,
        tts_voice_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> WorkflowRecordingModel:
        """Create a new workflow recording record.

        Args:
            recording_id: Short unique recording identifier
            organization_id: ID of the organization
            transcript: User-provided transcript
            storage_key: S3/MinIO storage key
            storage_backend: Storage backend (s3 or minio)
            created_by: ID of the user
            workflow_id: Optional workflow ID (legacy)
            tts_provider: Optional TTS provider name
            tts_model: Optional TTS model name
            tts_voice_id: Optional TTS voice identifier
            metadata: Optional extra metadata

        Returns:
            The created WorkflowRecordingModel
        """
        async with self.async_session() as session:
            recording = WorkflowRecordingModel(
                recording_id=recording_id,
                workflow_id=workflow_id,
                organization_id=organization_id,
                tts_provider=tts_provider,
                tts_model=tts_model,
                tts_voice_id=tts_voice_id,
                transcript=transcript,
                storage_key=storage_key,
                storage_backend=storage_backend,
                created_by=created_by,
                recording_metadata=metadata or {},
            )

            session.add(recording)
            await session.commit()
            await session.refresh(recording)

            logger.info(f"Created recording {recording_id} for org {organization_id}")
            return recording

    async def get_recordings(
        self,
        organization_id: int,
        workflow_id: Optional[int] = None,
        tts_provider: Optional[str] = None,
        tts_model: Optional[str] = None,
        tts_voice_id: Optional[str] = None,
    ) -> List[WorkflowRecordingModel]:
        """Get recordings for an organization, optionally filtered.

        Args:
            organization_id: ID of the organization
            workflow_id: Optional workflow ID filter
            tts_provider: Optional TTS provider filter
            tts_model: Optional TTS model filter
            tts_voice_id: Optional TTS voice ID filter

        Returns:
            List of WorkflowRecordingModel instances
        """
        async with self.async_session() as session:
            query = select(WorkflowRecordingModel).where(
                WorkflowRecordingModel.organization_id == organization_id,
                WorkflowRecordingModel.is_active == True,
            )

            if workflow_id is not None:
                query = query.where(WorkflowRecordingModel.workflow_id == workflow_id)
            if tts_provider:
                query = query.where(WorkflowRecordingModel.tts_provider == tts_provider)
            if tts_model:
                query = query.where(WorkflowRecordingModel.tts_model == tts_model)
            if tts_voice_id:
                query = query.where(WorkflowRecordingModel.tts_voice_id == tts_voice_id)

            query = query.order_by(WorkflowRecordingModel.created_at.desc())

            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_recording_by_recording_id(
        self,
        recording_id: str,
        organization_id: int,
    ) -> Optional[WorkflowRecordingModel]:
        """Get a recording by its short ID.

        Args:
            recording_id: The short unique recording ID
            organization_id: ID of the organization

        Returns:
            WorkflowRecordingModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = select(WorkflowRecordingModel).where(
                WorkflowRecordingModel.recording_id == recording_id,
                WorkflowRecordingModel.organization_id == organization_id,
                WorkflowRecordingModel.is_active == True,
            )

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def get_recording_by_id(
        self,
        id: int,
        organization_id: int,
    ) -> Optional[WorkflowRecordingModel]:
        """Get a recording by its integer primary key.

        Args:
            id: The primary key ID
            organization_id: ID of the organization

        Returns:
            WorkflowRecordingModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = select(WorkflowRecordingModel).where(
                WorkflowRecordingModel.id == id,
                WorkflowRecordingModel.organization_id == organization_id,
                WorkflowRecordingModel.is_active == True,
            )

            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def has_active_recordings(
        self,
        organization_id: int,
    ) -> bool:
        """Check if an organization has any active recordings.

        Args:
            organization_id: ID of the organization

        Returns:
            True if at least one active recording exists, False otherwise
        """
        async with self.async_session() as session:
            query = (
                select(func.count())
                .select_from(WorkflowRecordingModel)
                .where(
                    WorkflowRecordingModel.organization_id == organization_id,
                    WorkflowRecordingModel.is_active == True,
                )
            )
            result = await session.execute(query)
            return result.scalar_one() > 0

    async def check_recording_id_exists(
        self, recording_id: str, organization_id: int
    ) -> bool:
        """Check if a recording ID already exists within an organization.

        Args:
            recording_id: The recording ID to check
            organization_id: ID of the organization

        Returns:
            True if exists, False otherwise
        """
        async with self.async_session() as session:
            query = select(WorkflowRecordingModel.id).where(
                WorkflowRecordingModel.recording_id == recording_id,
                WorkflowRecordingModel.organization_id == organization_id,
                WorkflowRecordingModel.is_active == True,
            )
            result = await session.execute(query)
            return result.scalar_one_or_none() is not None

    async def update_recording_id(
        self,
        id: int,
        new_recording_id: str,
        organization_id: int,
    ) -> Optional[WorkflowRecordingModel]:
        """Update the recording_id of a recording.

        Args:
            id: Primary key ID of the recording
            new_recording_id: New recording ID
            organization_id: ID of the organization

        Returns:
            Updated WorkflowRecordingModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = select(WorkflowRecordingModel).where(
                WorkflowRecordingModel.id == id,
                WorkflowRecordingModel.organization_id == organization_id,
                WorkflowRecordingModel.is_active == True,
            )
            result = await session.execute(query)
            recording = result.scalar_one_or_none()

            if not recording:
                return None

            old_id = recording.recording_id
            recording.recording_id = new_recording_id
            await session.commit()
            await session.refresh(recording)

            logger.info(
                f"Updated recording ID {old_id} -> {new_recording_id}, "
                f"org {organization_id}"
            )
            return recording

    async def replace_recording_id_in_workflows(
        self,
        old_id: str,
        new_id: str,
        organization_id: int,
    ) -> int:
        """Replace all occurrences of a recording ID in workflow definitions.

        Updates both draft definitions (workflows.workflow_definition) and
        versioned definitions (workflow_definitions.workflow_json), skipping
        workflow_definitions with status 'legacy'.

        Args:
            old_id: The old recording ID to find
            new_id: The new recording ID to replace with
            organization_id: ID of the organization (scopes to org workflows)

        Returns:
            Total number of rows updated across both tables
        """
        # Match the exact pattern used in prompts: "RECORDING_ID: <id>"
        old_pattern = f"RECORDING_ID: {old_id}"
        new_pattern = f"RECORDING_ID: {new_id}"

        total = 0
        async with self.async_session() as session:
            # Update workflows.workflow_definition (draft definitions)
            result = await session.execute(
                text("""
                    UPDATE workflows
                    SET workflow_definition =
                        REPLACE(workflow_definition::text, :old_pat, :new_pat)::json
                    WHERE organization_id = :org_id
                      AND workflow_definition::text LIKE '%%' || :old_pat || '%%'
                """),
                {
                    "old_pat": old_pattern,
                    "new_pat": new_pattern,
                    "org_id": organization_id,
                },
            )
            total += result.rowcount

            # Update workflow_definitions.workflow_json (versioned definitions)
            # Skip legacy definitions
            result = await session.execute(
                text("""
                    UPDATE workflow_definitions wd
                    SET workflow_json =
                        REPLACE(wd.workflow_json::text, :old_pat, :new_pat)::json
                    FROM workflows w
                    WHERE wd.workflow_id = w.id
                      AND w.organization_id = :org_id
                      AND wd.status != 'legacy'
                      AND wd.workflow_json::text LIKE '%%' || :old_pat || '%%'
                """),
                {
                    "old_pat": old_pattern,
                    "new_pat": new_pattern,
                    "org_id": organization_id,
                },
            )
            total += result.rowcount

            await session.commit()

            if total > 0:
                logger.info(
                    f"Replaced recording ID '{old_id}' -> '{new_id}' "
                    f"in {total} workflow definition(s), org {organization_id}"
                )

        return total

    async def delete_recording(
        self,
        recording_id: str,
        organization_id: int,
    ) -> bool:
        """Soft delete a recording.

        Args:
            recording_id: The short recording ID
            organization_id: ID of the organization

        Returns:
            True if deleted, False if not found
        """
        async with self.async_session() as session:
            query = select(WorkflowRecordingModel).where(
                WorkflowRecordingModel.recording_id == recording_id,
                WorkflowRecordingModel.organization_id == organization_id,
            )

            result = await session.execute(query)
            recording = result.scalar_one_or_none()

            if not recording:
                return False

            recording.is_active = False
            await session.commit()

            logger.info(
                f"Deleted recording {recording_id} for organization {organization_id}"
            )
            return True
