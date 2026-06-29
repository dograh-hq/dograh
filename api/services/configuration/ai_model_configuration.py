from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Literal

from loguru import logger
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from api.constants import MPS_API_URL
from api.db import db_client
from api.db.models import WorkflowDefinitionModel, WorkflowModel
from api.enums import OrganizationConfigurationKey
from api.schemas.ai_model_configuration import (
    DOGRAH_DEFAULT_LANGUAGE,
    DOGRAH_DEFAULT_VOICE,
    DOGRAH_SPEED_MAX,
    DOGRAH_SPEED_MIN,
    BYOKAIModelConfiguration,
    BYOKPipelineAIModelConfiguration,
    BYOKRealtimeAIModelConfiguration,
    DograhManagedAIModelConfiguration,
    EffectiveAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration.masking import (
    SERVICE_SECRET_FIELDS,
    contains_masked_key,
    mask_key,
    resolve_masked_api_keys,
)
from api.services.configuration.registry import ServiceProviders
from api.services.configuration.resolve import resolve_effective_config

AIModelConfigurationSource = Literal["organization_v2", "legacy_user_v1", "empty"]
WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY = "model_configuration_v2_override"


@dataclass
class ResolvedAIModelConfiguration:
    effective: EffectiveAIModelConfiguration
    source: AIModelConfigurationSource
    organization_configuration: OrganizationAIModelConfigurationV2 | None = None


@dataclass
class WorkflowAIModelConfigurationMigrationResult:
    workflow_count: int = 0
    definition_count: int = 0
    workflow_ids: list[int] | None = None


async def get_resolved_ai_model_configuration(
    *,
    user_id: int | None,
    organization_id: int | None,
) -> ResolvedAIModelConfiguration:
    organization_configuration = await get_organization_ai_model_configuration_v2(
        organization_id
    )
    if organization_configuration is not None:
        return ResolvedAIModelConfiguration(
            effective=compile_ai_model_configuration_v2(organization_configuration),
            source="organization_v2",
            organization_configuration=organization_configuration,
        )

    if user_id is None:
        return ResolvedAIModelConfiguration(
            effective=EffectiveAIModelConfiguration(),
            source="empty",
        )

    legacy = await db_client.get_user_configurations(user_id)
    return ResolvedAIModelConfiguration(
        effective=legacy,
        source="legacy_user_v1" if _has_model_services(legacy) else "empty",
    )


async def get_effective_ai_model_configuration_for_workflow(
    *,
    user_id: int | None,
    organization_id: int | None,
    workflow_configurations: dict | None,
) -> EffectiveAIModelConfiguration:
    workflow_configurations = workflow_configurations or {}
    v2_override = workflow_configurations.get(
        WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY
    )
    if v2_override:
        return compile_ai_model_configuration_v2(
            OrganizationAIModelConfigurationV2.model_validate(v2_override)
        )

    resolved_config = await get_resolved_ai_model_configuration(
        user_id=user_id,
        organization_id=organization_id,
    )
    return resolve_effective_config(
        resolved_config.effective,
        workflow_configurations.get("model_overrides"),
    )


async def get_organization_ai_model_configuration_v2(
    organization_id: int | None,
) -> OrganizationAIModelConfigurationV2 | None:
    if organization_id is None:
        return None
    row = await db_client.get_configuration(
        organization_id,
        OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
    )
    if row is None or not row.value:
        return None
    try:
        return OrganizationAIModelConfigurationV2.model_validate(row.value)
    except ValidationError as exc:
        logger.warning(
            "Invalid org AI model configuration v2 for organization "
            f"{organization_id}: {exc}. Falling back to legacy configuration."
        )
        return None


async def upsert_organization_ai_model_configuration_v2(
    organization_id: int,
    configuration: OrganizationAIModelConfigurationV2,
) -> OrganizationAIModelConfigurationV2:
    await db_client.upsert_configuration(
        organization_id,
        OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
        configuration.model_dump(mode="json", exclude_none=True),
    )
    return configuration


async def migrate_workflow_model_configurations_to_v2(
    *,
    organization_id: int,
    fallback_user_config: EffectiveAIModelConfiguration,
) -> WorkflowAIModelConfigurationMigrationResult:
    workflows = await _list_workflows_for_model_configuration_migration(organization_id)
    owner_configs: dict[int, EffectiveAIModelConfiguration] = {}
    workflow_updates: list[tuple[int, dict]] = []
    definition_updates: list[tuple[int, dict]] = []
    migrated_workflow_ids: set[int] = set()

    for workflow in workflows:
        base_config = fallback_user_config
        if workflow.user_id is not None:
            if workflow.user_id not in owner_configs:
                owner_configs[
                    workflow.user_id
                ] = await db_client.get_user_configurations(workflow.user_id)
            base_config = owner_configs[workflow.user_id]

        workflow_configs, workflow_changed = (
            migrate_workflow_configuration_model_override_to_v2(
                workflow.workflow_configurations,
                base_config,
            )
        )
        if workflow_changed:
            workflow_updates.append((workflow.id, workflow_configs))
            migrated_workflow_ids.add(workflow.id)

        for definition in workflow.definitions:
            definition_configs, definition_changed = (
                migrate_workflow_configuration_model_override_to_v2(
                    definition.workflow_configurations,
                    base_config,
                )
            )
            if definition_changed:
                definition_updates.append((definition.id, definition_configs))
                migrated_workflow_ids.add(workflow.id)

    if workflow_updates or definition_updates:
        async with db_client.async_session() as session:
            for workflow_id, workflow_configs in workflow_updates:
                await session.execute(
                    update(WorkflowModel)
                    .where(WorkflowModel.id == workflow_id)
                    .values(workflow_configurations=workflow_configs)
                )
            for definition_id, definition_configs in definition_updates:
                await session.execute(
                    update(WorkflowDefinitionModel)
                    .where(WorkflowDefinitionModel.id == definition_id)
                    .values(workflow_configurations=definition_configs)
                )
            await session.commit()

    return WorkflowAIModelConfigurationMigrationResult(
        workflow_count=len(migrated_workflow_ids),
        definition_count=len(definition_updates),
        workflow_ids=sorted(migrated_workflow_ids),
    )


def migrate_workflow_configuration_model_override_to_v2(
    workflow_configurations: dict | None,
    base_config: EffectiveAIModelConfiguration,
) -> tuple[dict, bool]:
    if not isinstance(workflow_configurations, dict):
        return {}, False

    migrated = copy.deepcopy(workflow_configurations)
    model_overrides = migrated.get("model_overrides")
    existing_v2_override = migrated.get(WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY)
    if not isinstance(model_overrides, dict):
        if "model_overrides" in migrated:
            migrated.pop("model_overrides", None)
            return migrated, True
        return migrated, False

    if not existing_v2_override:
        effective = resolve_effective_config(base_config, model_overrides)
        v2_override = convert_legacy_ai_model_configuration_to_v2(effective)
        migrated[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY] = v2_override.model_dump(
            mode="json", exclude_none=True
        )
    migrated.pop("model_overrides", None)
    return migrated, True


def merge_ai_model_configuration_v2_secrets(
    incoming: OrganizationAIModelConfigurationV2,
    existing: OrganizationAIModelConfigurationV2 | None,
) -> OrganizationAIModelConfigurationV2:
    if existing is None:
        return incoming

    incoming_dict = incoming.model_dump(mode="json", exclude_none=True)
    existing_dict = existing.model_dump(mode="json", exclude_none=True)

    if incoming_dict.get("mode") == "dograh" and existing_dict.get("mode") == "dograh":
        incoming_dograh = incoming_dict.get("dograh") or {}
        existing_dograh = existing_dict.get("dograh") or {}
        incoming_key = incoming_dograh.get("api_key")
        existing_key = existing_dograh.get("api_key")
        if incoming_key and existing_key and contains_masked_key(incoming_key):
            incoming_dograh["api_key"] = resolve_masked_api_keys(
                incoming_key,
                existing_key,
            )

    if incoming_dict.get("mode") == "byok" and existing_dict.get("mode") == "byok":
        _merge_byok_secret_fields(incoming_dict.get("byok"), existing_dict.get("byok"))

    return OrganizationAIModelConfigurationV2.model_validate(incoming_dict)


def check_for_masked_keys_in_ai_model_configuration_v2(
    configuration: OrganizationAIModelConfigurationV2,
) -> None:
    data = configuration.model_dump(mode="json", exclude_none=True)
    _raise_if_masked_secret(data)


def mask_ai_model_configuration_v2(
    configuration: OrganizationAIModelConfigurationV2 | None,
) -> dict | None:
    if configuration is None:
        return None
    data = configuration.model_dump(mode="json", exclude_none=True)
    _mask_secret_fields(data)
    return data


def convert_legacy_ai_model_configuration_to_v2(
    configuration: EffectiveAIModelConfiguration,
) -> OrganizationAIModelConfigurationV2:
    dograh_key = _first_dograh_api_key(configuration)
    if dograh_key:
        return _convert_any_dograh_legacy_configuration(configuration, dograh_key)

    if configuration.is_realtime:
        if configuration.realtime is None or configuration.llm is None:
            raise ValueError("Realtime legacy configuration is incomplete")
        return OrganizationAIModelConfigurationV2(
            mode="byok",
            byok=BYOKAIModelConfiguration(
                mode="realtime",
                realtime=BYOKRealtimeAIModelConfiguration(
                    realtime=configuration.realtime,
                    llm=configuration.llm,
                    embeddings=configuration.embeddings,
                ),
            ),
        )

    if (
        configuration.llm is None
        or configuration.tts is None
        or configuration.stt is None
    ):
        raise ValueError("Pipeline legacy configuration is incomplete")
    return OrganizationAIModelConfigurationV2(
        mode="byok",
        byok=BYOKAIModelConfiguration(
            mode="pipeline",
            pipeline=BYOKPipelineAIModelConfiguration(
                llm=configuration.llm,
                tts=configuration.tts,
                stt=configuration.stt,
                embeddings=configuration.embeddings,
            ),
        ),
    )


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


def _merge_byok_secret_fields(incoming_byok: dict | None, existing_byok: dict | None):
    if not isinstance(incoming_byok, dict) or not isinstance(existing_byok, dict):
        return
    incoming_mode = incoming_byok.get("mode")
    existing_mode = existing_byok.get("mode")
    if incoming_mode != existing_mode:
        return
    section_names = (
        ("llm", "tts", "stt", "embeddings")
        if incoming_mode == "pipeline"
        else ("realtime", "llm", "embeddings")
    )
    incoming_container = incoming_byok.get(incoming_mode)
    existing_container = existing_byok.get(existing_mode)
    if not isinstance(incoming_container, dict) or not isinstance(
        existing_container, dict
    ):
        return
    for section_name in section_names:
        incoming_section = incoming_container.get(section_name)
        existing_section = existing_container.get(section_name)
        if isinstance(incoming_section, dict) and isinstance(existing_section, dict):
            _merge_service_secret_fields(incoming_section, existing_section)


async def _list_workflows_for_model_configuration_migration(
    organization_id: int,
) -> list[WorkflowModel]:
    async with db_client.async_session() as session:
        result = await session.execute(
            select(WorkflowModel)
            .options(selectinload(WorkflowModel.definitions))
            .where(WorkflowModel.organization_id == organization_id)
        )
        return list(result.scalars().unique().all())


def _merge_service_secret_fields(incoming: dict, existing: dict):
    if (
        incoming.get("provider") is not None
        and existing.get("provider") is not None
        and incoming.get("provider") != existing.get("provider")
    ):
        return
    for secret_field in SERVICE_SECRET_FIELDS:
        if secret_field not in existing:
            continue
        incoming_secret = incoming.get(secret_field)
        existing_secret = existing[secret_field]
        if incoming_secret is None:
            incoming[secret_field] = existing_secret
        elif contains_masked_key(incoming_secret):
            incoming[secret_field] = resolve_masked_api_keys(
                incoming_secret,
                existing_secret,
            )


def _raise_if_masked_secret(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in SERVICE_SECRET_FIELDS and contains_masked_key(nested):
                raise ValueError(
                    f"The {key} appears to be masked. Please provide the actual "
                    "value, not the masked value."
                )
            _raise_if_masked_secret(nested)
    elif isinstance(value, list):
        for item in value:
            _raise_if_masked_secret(item)


def _mask_secret_fields(value):
    if isinstance(value, dict):
        for key, nested in list(value.items()):
            if key in SERVICE_SECRET_FIELDS and nested:
                value[key] = _mask_secret_value(nested)
            else:
                _mask_secret_fields(nested)
    elif isinstance(value, list):
        for item in value:
            _mask_secret_fields(item)


def _mask_secret_value(value):
    if isinstance(value, list):
        return [mask_key(item) for item in value]
    return mask_key(value)


def _has_model_services(configuration: EffectiveAIModelConfiguration) -> bool:
    return any(
        service is not None
        for service in (
            configuration.llm,
            configuration.tts,
            configuration.stt,
            configuration.embeddings,
            configuration.realtime,
        )
    )


def _convert_any_dograh_legacy_configuration(
    configuration: EffectiveAIModelConfiguration,
    dograh_key: str,
) -> OrganizationAIModelConfigurationV2:
    speed = getattr(configuration.tts, "speed", 1.0)
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = 1.0
    if not DOGRAH_SPEED_MIN <= speed <= DOGRAH_SPEED_MAX:
        speed = 1.0
    return OrganizationAIModelConfigurationV2(
        mode="dograh",
        dograh=DograhManagedAIModelConfiguration(
            api_key=dograh_key,
            voice=getattr(configuration.tts, "voice", DOGRAH_DEFAULT_VOICE)
            or DOGRAH_DEFAULT_VOICE,
            speed=speed,
            language=getattr(configuration.stt, "language", DOGRAH_DEFAULT_LANGUAGE)
            or DOGRAH_DEFAULT_LANGUAGE,
        ),
    )


def _first_dograh_api_key(configuration: EffectiveAIModelConfiguration) -> str | None:
    for service in (
        configuration.llm,
        configuration.tts,
        configuration.stt,
        configuration.embeddings,
        configuration.realtime,
    ):
        if service is None or _provider(service) != ServiceProviders.DOGRAH:
            continue
        try:
            return _single_api_key(service)
        except ValueError:
            continue
    return None


def _provider(service):
    return getattr(service, "provider", None)


def _single_api_key(service) -> str:
    if hasattr(service, "get_all_api_keys"):
        keys = service.get_all_api_keys()
        if len(keys) != 1:
            raise ValueError("Expected exactly one API key")
        return keys[0]
    key = getattr(service, "api_key", None)
    if not key:
        raise ValueError("Expected an API key")
    return key
