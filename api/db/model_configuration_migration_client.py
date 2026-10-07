"""Transactional data access for the bounded model-catalog normalization job."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from api.db.base_client import BaseDBClient
from api.db.models import (
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
from api.schemas.model_configuration_migration import (
    MODEL_CONFIGURATION_CATALOG_BOOTSTRAP_KEY,
    MODEL_CONFIGURATION_DEFAULT_KEY,
    MODEL_CONFIGURATION_MIGRATION_KEY,
    MigrationDefinition,
    MigrationWorkflow,
    ModelConfigurationMigrationPlan,
    ModelConfigurationMigrationSource,
)


class ModelConfigurationMigrationClient(BaseDBClient):
    async def list_model_configuration_migration_organization_ids(
        self,
        *,
        organization_ids: list[int] | None = None,
        after_organization_id: int = 0,
        limit: int = 100,
    ) -> list[int]:
        if not 1 <= limit <= 1000:
            raise ValueError("migration limit must be between 1 and 1000")
        statement = select(OrganizationModel.id).where(
            OrganizationModel.id > after_organization_id
        )
        if organization_ids is not None:
            statement = statement.where(OrganizationModel.id.in_(organization_ids))
        async with self.async_session() as session:
            return list(
                (
                    await session.scalars(
                        statement.order_by(OrganizationModel.id).limit(limit)
                    )
                ).all()
            )

    async def run_model_configuration_migration_transaction(
        self,
        organization_id: int,
        planner: Callable[
            [ModelConfigurationMigrationSource], ModelConfigurationMigrationPlan
        ],
        *,
        apply: bool = False,
        include_workflows: bool = True,
    ) -> ModelConfigurationMigrationPlan:
        """Plan and optionally commit one org, keeping source reads and writes atomic.

        Dry runs use a read-only repeatable-read transaction. Apply locks the org
        and all source rows before invoking a pure planner; losing a concurrent
        default insertion aborts the whole organization. Completed migrations
        are never replayed against later edits. Runtime compatibility imports
        use the same org lock, so exact connection deduplication is race safe.
        """
        try:
            async with self.async_session() as session:
                # A fresh session owns its transaction. Under a caller-managed
                # one (the test harness shares a single session) nest instead;
                # the isolation level is then already fixed by the outer scope.
                nested = session.in_transaction()
                async with session.begin_nested() if nested else session.begin():
                    if not apply and not nested:
                        await session.execute(
                            text(
                                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                            )
                        )
                    organization_query = select(OrganizationModel.id).where(
                        OrganizationModel.id == organization_id
                    )
                    if apply:
                        organization_query = organization_query.with_for_update()
                    if (await session.scalar(organization_query)) is None:
                        return ModelConfigurationMigrationPlan(
                            organization_id,
                            status="blocked",
                            issues=[{"reason": "organization_not_found"}],
                        )
                    source = await self._read_model_configuration_migration_source(
                        session,
                        organization_id,
                        lock=apply,
                        include_workflows=include_workflows,
                    )
                    plan = planner(source)
                    if not apply or plan.status != "planned":
                        return plan
                    await self._apply_model_configuration_migration_plan(
                        session, source, plan
                    )
                plan.status = "applied"
                return plan
        except SQLAlchemyError:
            # SQLAlchemy errors can include SQL parameter values (credentials).
            raise RuntimeError("model_configuration_migration_database_error") from None

    @staticmethod
    async def _read_model_configuration_migration_source(
        session, organization_id, *, lock, include_workflows
    ):
        def locked(statement):
            return statement.with_for_update() if lock else statement

        configuration_rows = (
            await session.scalars(
                locked(
                    select(OrganizationConfigurationModel).where(
                        OrganizationConfigurationModel.organization_id
                        == organization_id,
                        OrganizationConfigurationModel.key.in_(
                            [
                                OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
                                MODEL_CONFIGURATION_DEFAULT_KEY,
                                MODEL_CONFIGURATION_MIGRATION_KEY,
                                MODEL_CONFIGURATION_CATALOG_BOOTSTRAP_KEY,
                            ]
                        ),
                    )
                )
            )
        ).all()
        values = {row.key: deepcopy(row.value) for row in configuration_rows}
        source = ModelConfigurationMigrationSource(
            organization_id=organization_id,
            organization_configuration=values.get(
                OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value
            ),
            default_configuration_uuid=values.get(MODEL_CONFIGURATION_DEFAULT_KEY),
            migration_complete=(
                values.get(MODEL_CONFIGURATION_MIGRATION_KEY) or {}
            ).get("status")
            == "completed",
            bootstrap_metadata=values.get(MODEL_CONFIGURATION_CATALOG_BOOTSTRAP_KEY),
        )
        connections = (
            await session.scalars(
                locked(
                    select(ProviderConnectionModel).where(
                        ProviderConnectionModel.organization_id == organization_id
                    )
                )
            )
        ).all()
        for row in connections:
            source.connections.append(
                {
                    "uuid": row.uuid,
                    "provider": row.provider,
                    "name": row.name,
                    "credentials": deepcopy(row.credentials),
                    "connection_settings": deepcopy(row.connection_settings),
                    "revision": row.revision,
                    "is_active": row.is_active,
                }
            )
        configurations = (
            await session.scalars(
                locked(
                    select(NamedModelConfigurationModel).where(
                        NamedModelConfigurationModel.organization_id == organization_id
                    )
                )
            )
        ).all()
        for row in configurations:
            source.configurations.append(
                {
                    "uuid": row.uuid,
                    "configuration": deepcopy(row.configuration),
                    "revision": row.revision,
                    "is_active": row.is_active,
                }
            )
        if not include_workflows:
            return source
        workflow_rows = (
            await session.execute(
                locked(
                    select(
                        WorkflowModel.id,
                        WorkflowModel.workflow_configurations,
                        WorkflowModel.released_definition_id,
                    ).where(WorkflowModel.organization_id == organization_id)
                )
            )
        ).all()
        workflows = {
            row.id: MigrationWorkflow(
                row.id,
                deepcopy(row.workflow_configurations or {}),
                row.released_definition_id,
            )
            for row in workflow_rows
        }
        definition_rows = (
            await session.execute(
                locked(
                    select(
                        WorkflowDefinitionModel.id,
                        WorkflowDefinitionModel.workflow_id,
                        WorkflowDefinitionModel.status,
                        WorkflowDefinitionModel.is_current,
                        WorkflowDefinitionModel.workflow_configurations,
                    )
                    .join(
                        WorkflowModel,
                        WorkflowDefinitionModel.workflow_id == WorkflowModel.id,
                    )
                    .where(WorkflowModel.organization_id == organization_id)
                )
            )
        ).all()
        for row in definition_rows:
            workflows[row.workflow_id].definitions.append(
                MigrationDefinition(
                    row.id,
                    row.workflow_id,
                    row.status,
                    deepcopy(row.workflow_configurations or {}),
                    row.is_current,
                )
            )
        source.workflows = list(workflows.values())
        # Inspect embedding identities without loading customer text or vectors.
        spaces = (
            await session.execute(
                select(
                    KnowledgeBaseChunkModel.embedding_model,
                    KnowledgeBaseChunkModel.embedding_dimension,
                    func.count(KnowledgeBaseChunkModel.id).label("chunk_count"),
                )
                .join(
                    KnowledgeBaseDocumentModel,
                    KnowledgeBaseDocumentModel.id
                    == KnowledgeBaseChunkModel.document_id,
                )
                .where(
                    KnowledgeBaseChunkModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.organization_id == organization_id,
                    KnowledgeBaseDocumentModel.is_active.is_(True),
                    KnowledgeBaseDocumentModel.retrieval_mode == "chunked",
                )
                .group_by(
                    KnowledgeBaseChunkModel.embedding_model,
                    KnowledgeBaseChunkModel.embedding_dimension,
                )
            )
        ).all()
        source.embedding_spaces = [
            {
                "model": row.embedding_model,
                "dimension": row.embedding_dimension,
                "chunk_count": row.chunk_count,
            }
            for row in spaces
        ]
        return source

    @staticmethod
    async def _apply_model_configuration_migration_plan(session, source, plan):
        if source.organization_id != plan.organization_id:
            raise ValueError("migration_organization_mismatch")
        organization_id = source.organization_id
        for values in plan.connections:
            session.add(
                ProviderConnectionModel(organization_id=organization_id, **values)
            )
        for values in plan.configurations:
            session.add(
                NamedModelConfigurationModel(organization_id=organization_id, **values)
            )
        await session.flush()
        for key, value in plan.organization_updates.items():
            # No replacement: a concurrent new writer wins; abort all catalog
            # inserts/workflow changes rather than clobbering its default.
            now = datetime.now(UTC)
            result = await session.execute(
                insert(OrganizationConfigurationModel)
                .values(
                    organization_id=organization_id,
                    key=key,
                    value=value,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(constraint="_organization_key_uc")
            )
            if result.rowcount != 1:
                raise ValueError("migration_configuration_changed_concurrently")
        for workflow_id, configuration in plan.workflow_updates:
            result = await session.execute(
                update(WorkflowModel)
                .where(
                    WorkflowModel.id == workflow_id,
                    WorkflowModel.organization_id == organization_id,
                )
                .values(workflow_configurations=configuration)
            )
            if result.rowcount != 1:
                raise ValueError("migration_workflow_changed_concurrently")
        organization_workflows = select(WorkflowModel.id).where(
            WorkflowModel.organization_id == organization_id
        )
        for definition_id, configuration in plan.definition_updates:
            result = await session.execute(
                update(WorkflowDefinitionModel)
                .where(
                    WorkflowDefinitionModel.id == definition_id,
                    WorkflowDefinitionModel.workflow_id.in_(organization_workflows),
                )
                .values(workflow_configurations=configuration)
            )
            if result.rowcount != 1:
                raise ValueError("migration_definition_changed_concurrently")
