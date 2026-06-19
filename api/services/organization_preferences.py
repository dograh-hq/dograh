from inspect import isawaitable

from loguru import logger
from pydantic import ValidationError

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.schemas.organization_preferences import OrganizationPreferences


async def get_organization_preferences(
    organization_id: int | None,
    db=None,
) -> OrganizationPreferences:
    if organization_id is None:
        return OrganizationPreferences()

    db = db or db_client
    row = await _get_configuration(
        db,
        organization_id,
        OrganizationConfigurationKey.ORGANIZATION_PREFERENCES.value,
    )
    if row is None:
        row = await _get_configuration(
            db,
            organization_id,
            OrganizationConfigurationKey.MODEL_CONFIGURATION_PREFERENCES.value,
        )
    return _parse_preferences(row.value if row is not None else None, organization_id)


async def upsert_organization_preferences(
    organization_id: int,
    preferences: OrganizationPreferences,
) -> OrganizationPreferences:
    await db_client.upsert_configuration(
        organization_id,
        OrganizationConfigurationKey.ORGANIZATION_PREFERENCES.value,
        preferences.model_dump(mode="json", exclude_none=True),
    )
    return preferences


async def _get_configuration(db, organization_id: int, key: str):
    row = db.get_configuration(organization_id, key)
    if isawaitable(row):
        row = await row
    return row


def _parse_preferences(value, organization_id: int) -> OrganizationPreferences:
    if not value or not isinstance(value, dict):
        return OrganizationPreferences()
    try:
        return OrganizationPreferences.model_validate(value)
    except ValidationError as exc:
        logger.warning(
            "Invalid organization preferences for organization "
            f"{organization_id}: {exc}. Returning defaults."
        )
        return OrganizationPreferences()
