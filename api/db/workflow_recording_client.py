"""Database client for managing workflow recordings."""

import secrets
import string
from typing import List, Optional

from loguru import logger
from sqlalchemy import func, select

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
        workflow_id: int,
        organization_id: int,
        tts_provider: str,
        tts_model: str,
        tts_voice_id: str,
        transcript: str,
        storage_key: str,
        storage_backend: str,
        created_by: int,
        metadata: Optional[dict] = None,
    ) -> WorkflowRecordingModel:
        """Create a new workflow recording record.

        Args:
            recording_id: Short unique recording identifier
            workflow_id: ID of the workflow
            organization_id: ID of the organization
            tts_provider: TTS provider name
            tts_model: TTS model name
            tts_voice_id: TTS voice identifier
            transcript: User-provided transcript
            storage_key: S3/MinIO storage key
            storage_backend: Storage backend (s3 or minio)
            created_by: ID of the user
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

            logger.info(
                f"Created recording {recording_id} for workflow {workflow_id}, "
                f"org {organization_id}"
            )
            return recording

    async def get_recordings(
        self,
        organization_id: int,
        workflow_id: Optional[int] = None,
        tts_provider: Optional[str] = None,
        tts_model: Optional[str] = None,
        tts_voice_id: Optional[str] = None,
    ) -> List[WorkflowRecordingModel]:
        """Get recordings for an organization, optionally filtered by workflow and TTS config.

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
        workflow_id: int,
    ) -> Optional[WorkflowRecordingModel]:
        """Get a recording by its string recording_id (unique per org + workflow).

        Args:
            recording_id: The descriptive recording ID
            organization_id: ID of the organization
            workflow_id: ID of the workflow

        Returns:
            WorkflowRecordingModel if found, None otherwise
        """
        async with self.async_session() as session:
            query = select(WorkflowRecordingModel).where(
                WorkflowRecordingModel.recording_id == recording_id,
                WorkflowRecordingModel.organization_id == organization_id,
                WorkflowRecordingModel.workflow_id == workflow_id,
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
        workflow_id: int,
        organization_id: int,
    ) -> bool:
        """Check if a workflow has any active recordings.

        Args:
            workflow_id: ID of the workflow
            organization_id: ID of the organization

        Returns:
            True if at least one active recording exists, False otherwise
        """
        async with self.async_session() as session:
            query = (
                select(func.count())
                .select_from(WorkflowRecordingModel)
                .where(
                    WorkflowRecordingModel.workflow_id == workflow_id,
                    WorkflowRecordingModel.organization_id == organization_id,
                    WorkflowRecordingModel.is_active == True,
                )
            )
            result = await session.execute(query)
            return result.scalar_one() > 0

    async def check_recording_id_exists(
        self, recording_id: str, organization_id: int, workflow_id: int
    ) -> bool:
        """Check if a recording ID already exists within an organization and workflow.

        Args:
            recording_id: The recording ID to check
            organization_id: ID of the organization
            workflow_id: ID of the workflow

        Returns:
            True if exists, False otherwise
        """
        async with self.async_session() as session:
            query = select(WorkflowRecordingModel.id).where(
                WorkflowRecordingModel.recording_id == recording_id,
                WorkflowRecordingModel.organization_id == organization_id,
                WorkflowRecordingModel.workflow_id == workflow_id,
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
