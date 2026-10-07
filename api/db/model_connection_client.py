"""Organization-scoped persistence for reusable model connections.

Catalog mutations serialize on the organization row. Credentials never leave this
client through an unscoped lookup. Only current credentials are retained.
"""

from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import select

from api.db.base_client import BaseDBClient
from api.db.models import (
    CampaignModel,
    KnowledgeBaseChunkModel,
    KnowledgeBaseDocumentModel,
    NamedModelConfigurationModel,
    OrganizationConfigurationModel,
    OrganizationModel,
    ProviderConnectionModel,
    WorkflowDefinitionModel,
    WorkflowModel,
)
from api.enums import OrganizationConfigurationKey
from api.schemas.model_connections import merge_provider_credentials

DEFAULT_KEY = OrganizationConfigurationKey.MODEL_CONFIGURATION_DEFAULT_UUID.value


class ModelCatalogNotFound(ValueError):
    pass


class ModelCatalogConflict(ValueError):
    pass


def _contains_reference(payload, uuid):
    if isinstance(payload, dict):
        return any(
            (
                key in {"provider_connection_uuid", "model_configuration_uuid"}
                and value == uuid
            )
            or _contains_reference(value, uuid)
            for key, value in payload.items()
        )
    if isinstance(payload, list):
        return any(_contains_reference(value, uuid) for value in payload)
    return False


class ModelConnectionClient(BaseDBClient):
    async def _lock_catalog(self, session, organization_id):
        found = await session.scalar(
            select(OrganizationModel.id)
            .where(OrganizationModel.id == organization_id)
            .with_for_update()
        )
        if found is None:
            raise ModelCatalogNotFound("Organization not found")

    async def _catalog_row(
        self, session, model, organization_id, uuid, active_only=True
    ):
        stmt = select(model).where(
            model.organization_id == organization_id, model.uuid == str(uuid)
        )
        if active_only:
            stmt = stmt.where(model.is_active.is_(True))
        return await session.scalar(stmt)

    async def list_provider_connections(self, organization_id, *, active_only=True):
        return await self._list_catalog(
            ProviderConnectionModel, organization_id, active_only
        )

    async def list_named_model_configurations(
        self, organization_id, *, active_only=True
    ):
        return await self._list_catalog(
            NamedModelConfigurationModel, organization_id, active_only
        )

    async def _list_catalog(self, model, organization_id, active_only):
        async with self.async_session() as session:
            stmt = (
                select(model)
                .where(model.organization_id == organization_id)
                .order_by(model.created_at, model.id)
            )
            if active_only:
                stmt = stmt.where(model.is_active.is_(True))
            return list((await session.scalars(stmt)).all())

    async def get_provider_connection(
        self, organization_id, connection_uuid, *, active_only=True
    ):
        async with self.async_session() as session:
            return await self._catalog_row(
                session,
                ProviderConnectionModel,
                organization_id,
                connection_uuid,
                active_only,
            )

    async def get_named_model_configuration(
        self, organization_id, configuration_uuid, *, active_only=True
    ):
        async with self.async_session() as session:
            return await self._catalog_row(
                session,
                NamedModelConfigurationModel,
                organization_id,
                configuration_uuid,
                active_only,
            )

    async def create_provider_connection(
        self,
        organization_id,
        *,
        name,
        provider,
        credentials,
        connection_settings,
    ):
        async with self.async_session() as session:
            await self._lock_catalog(session, organization_id)
            row = ProviderConnectionModel(
                organization_id=organization_id,
                name=name,
                provider=provider,
                credentials=deepcopy(credentials),
                connection_settings=deepcopy(connection_settings),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def update_provider_connection(
        self, organization_id, connection_uuid, *, changes, expected_revision=None
    ):
        async with self.async_session() as session:
            await self._lock_catalog(session, organization_id)
            row = await self._catalog_row(
                session, ProviderConnectionModel, organization_id, connection_uuid
            )
            if row is None:
                raise ModelCatalogNotFound("Provider connection not found")
            if expected_revision is not None and row.revision != expected_revision:
                raise ModelCatalogConflict(
                    "Provider connection changed; refresh and retry"
                )
            updates = {
                key: deepcopy(changes[key])
                for key in ("name", "connection_settings")
                if key in changes
            }
            if "credentials" in changes:
                updates["credentials"] = merge_provider_credentials(
                    row.credentials, deepcopy(changes["credentials"])
                )
            if all(getattr(row, key) == value for key, value in updates.items()):
                return row
            for key, value in updates.items():
                setattr(row, key, value)
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return row

    async def _check_connection_references(
        self, session, organization_id, configuration
    ):
        selections = [
            configuration.get(role)
            for role in ("llm", "tts", "stt", "realtime", "embeddings")
        ]
        selections.extend(
            rule["target"]
            for rule in (configuration.get("llm_fallback") or {}).get("rules", [])
        )
        for selection in selections:
            if selection:
                row = await self._catalog_row(
                    session,
                    ProviderConnectionModel,
                    organization_id,
                    selection["provider_connection_uuid"],
                )
                if row is None:
                    raise ModelCatalogNotFound("Provider connection not found")

    async def create_named_model_configuration(
        self, organization_id, *, name, configuration
    ):
        async with self.async_session() as session:
            await self._lock_catalog(session, organization_id)
            await self._check_connection_references(
                session, organization_id, configuration
            )
            row = NamedModelConfigurationModel(
                organization_id=organization_id,
                name=name,
                configuration=deepcopy(configuration),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def update_named_model_configuration(
        self, organization_id, configuration_uuid, *, changes, expected_revision=None
    ):
        async with self.async_session() as session:
            await self._lock_catalog(session, organization_id)
            row = await self._catalog_row(
                session,
                NamedModelConfigurationModel,
                organization_id,
                configuration_uuid,
            )
            if row is None:
                raise ModelCatalogNotFound("Model configuration not found")
            if expected_revision is not None and row.revision != expected_revision:
                raise ModelCatalogConflict(
                    "Model configuration changed; refresh and retry"
                )
            if "configuration" in changes:
                await self._check_connection_references(
                    session, organization_id, changes["configuration"]
                )
            updates = {
                key: deepcopy(changes[key])
                for key in ("name", "configuration")
                if key in changes
            }
            if all(getattr(row, key) == value for key, value in updates.items()):
                return row
            for key, value in updates.items():
                setattr(row, key, value)
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return row

    @staticmethod
    async def _write_default_pointer(session, organization_id, configuration_uuid):
        config = await session.scalar(
            select(OrganizationConfigurationModel).where(
                OrganizationConfigurationModel.organization_id == organization_id,
                OrganizationConfigurationModel.key == DEFAULT_KEY,
            )
        )
        if config is None:
            session.add(
                OrganizationConfigurationModel(
                    organization_id=organization_id,
                    key=DEFAULT_KEY,
                    value=str(configuration_uuid),
                )
            )
        else:
            config.value = str(configuration_uuid)
            config.updated_at = datetime.now(UTC)

    async def bootstrap_default_model_configuration(
        self,
        organization_id,
        *,
        connection_name,
        provider,
        credentials,
        configuration_name,
        configuration_for,
    ):
        """Create one connection, one configuration on it, and make it the default.

        All three land in one transaction so a crash cannot leave a connection
        without the configuration that makes it usable. ``configuration_for``
        receives the new connection's uuid and returns the specification.
        """
        async with self.async_session() as session:
            await self._lock_catalog(session, organization_id)
            connection = ProviderConnectionModel(
                organization_id=organization_id,
                name=connection_name,
                provider=provider,
                credentials=deepcopy(credentials),
                connection_settings={},
            )
            session.add(connection)
            await session.flush()
            configuration = NamedModelConfigurationModel(
                organization_id=organization_id,
                name=configuration_name,
                configuration=configuration_for(connection.uuid),
            )
            session.add(configuration)
            await session.flush()
            await self._write_default_pointer(
                session, organization_id, configuration.uuid
            )
            await session.commit()
            return configuration.uuid

    async def set_default_named_model_configuration(
        self, organization_id, configuration_uuid, *, expected_revision=None
    ):
        async with self.async_session() as session:
            await self._lock_catalog(session, organization_id)
            row = await self._catalog_row(
                session,
                NamedModelConfigurationModel,
                organization_id,
                configuration_uuid,
            )
            if row is None:
                raise ModelCatalogNotFound("Model configuration not found")
            if expected_revision is not None and row.revision != expected_revision:
                raise ModelCatalogConflict(
                    "Model configuration changed; refresh and retry"
                )
            await self._write_default_pointer(
                session, organization_id, configuration_uuid
            )
            await session.commit()

    async def archive_provider_connection(self, organization_id, connection_uuid):
        await self._archive_catalog(
            ProviderConnectionModel, organization_id, connection_uuid
        )

    async def archive_named_model_configuration(
        self, organization_id, configuration_uuid
    ):
        await self._archive_catalog(
            NamedModelConfigurationModel, organization_id, configuration_uuid
        )

    async def _archive_catalog(self, model, organization_id, uuid):
        uuid = str(uuid)
        async with self.async_session() as session:
            await self._lock_catalog(session, organization_id)
            row = await self._catalog_row(session, model, organization_id, uuid)
            if row is None:
                raise ModelCatalogNotFound("Configuration not found")
            default = await session.scalar(
                select(OrganizationConfigurationModel.value).where(
                    OrganizationConfigurationModel.organization_id == organization_id,
                    OrganizationConfigurationModel.key == DEFAULT_KEY,
                )
            )
            if model is NamedModelConfigurationModel and default == uuid:
                raise ModelCatalogConflict(
                    "Select another organization default before archiving"
                )
            if model is ProviderConnectionModel:
                named = await session.scalars(
                    select(NamedModelConfigurationModel.configuration).where(
                        NamedModelConfigurationModel.organization_id == organization_id,
                        NamedModelConfigurationModel.is_active.is_(True),
                    )
                )
                if any(_contains_reference(config, uuid) for config in named):
                    raise ModelCatalogConflict(
                        "Provider connection is used by a model configuration"
                    )
            workflows = await session.scalars(
                select(WorkflowModel.workflow_configurations).where(
                    WorkflowModel.organization_id == organization_id,
                    WorkflowModel.status == "active",
                )
            )
            definitions = await session.scalars(
                select(WorkflowDefinitionModel.workflow_configurations)
                .join(
                    WorkflowModel,
                    WorkflowDefinitionModel.workflow_id == WorkflowModel.id,
                )
                .where(
                    WorkflowModel.organization_id == organization_id,
                    WorkflowModel.status == "active",
                    WorkflowDefinitionModel.status.in_(("draft", "published")),
                )
            )
            if any(
                _contains_reference(config, uuid)
                for config in [*workflows, *definitions]
            ):
                raise ModelCatalogConflict(
                    "Configuration is used by an active workflow"
                )
            campaign_metadata = await session.scalars(
                select(CampaignModel.orchestrator_metadata).where(
                    CampaignModel.organization_id == organization_id,
                    CampaignModel.state.not_in(("completed", "failed")),
                )
            )
            pinned_definition_ids = {
                variant["workflow_definition_id"]
                for metadata in campaign_metadata
                for variant in ((metadata or {}).get("traffic_split") or {}).get(
                    "variants", []
                )
                if isinstance(variant.get("workflow_definition_id"), int)
            }
            if pinned_definition_ids:
                pinned = await session.scalars(
                    select(WorkflowDefinitionModel.workflow_configurations)
                    .join(
                        WorkflowModel,
                        WorkflowDefinitionModel.workflow_id == WorkflowModel.id,
                    )
                    .where(
                        WorkflowModel.organization_id == organization_id,
                        WorkflowDefinitionModel.id.in_(pinned_definition_ids),
                    )
                )
                if any(_contains_reference(config, uuid) for config in pinned):
                    raise ModelCatalogConflict(
                        "Configuration is used by a campaign's pinned workflow version"
                    )
            row.is_active = False
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            await session.commit()

    async def restore_provider_connection(self, organization_id, connection_uuid):
        return await self._restore_catalog(
            ProviderConnectionModel, organization_id, connection_uuid
        )

    async def restore_named_model_configuration(
        self, organization_id, configuration_uuid, *, expected_revision=None
    ):
        return await self._restore_catalog(
            NamedModelConfigurationModel,
            organization_id,
            configuration_uuid,
            expected_revision=expected_revision,
        )

    async def _restore_catalog(
        self, model, organization_id, uuid, *, expected_revision=None
    ):
        async with self.async_session() as session:
            await self._lock_catalog(session, organization_id)
            row = await self._catalog_row(
                session, model, organization_id, uuid, active_only=False
            )
            if row is None:
                raise ModelCatalogNotFound("Configuration not found")
            if expected_revision is not None and row.revision != expected_revision:
                raise ModelCatalogConflict("Configuration changed; refresh and retry")
            if not row.is_active:
                if model is NamedModelConfigurationModel:
                    # Serialize with provider archiving so a restored model never
                    # becomes selectable with already-archived connections.
                    await self._check_connection_references(
                        session, organization_id, row.configuration
                    )
                row.is_active = True
                row.revision += 1
                row.updated_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(row)
            return row

    async def get_model_configuration_embedding_spaces(
        self, organization_id, *, document_uuids=None
    ):
        if document_uuids == []:
            return []
        async with self.async_session() as session:
            statement = (
                select(
                    KnowledgeBaseChunkModel.embedding_model,
                    KnowledgeBaseChunkModel.embedding_dimension,
                )
                .join(
                    KnowledgeBaseDocumentModel,
                    KnowledgeBaseChunkModel.document_id
                    == KnowledgeBaseDocumentModel.id,
                )
                .where(
                    KnowledgeBaseChunkModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.is_active.is_(True),
                    KnowledgeBaseDocumentModel.retrieval_mode == "chunked",
                )
                .distinct()
            )
            if document_uuids is not None:
                statement = statement.where(
                    KnowledgeBaseDocumentModel.document_uuid.in_(document_uuids)
                )
            return [
                {"model": model, "dimension": dimension}
                for model, dimension in (await session.execute(statement)).all()
            ]
