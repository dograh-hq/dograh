from typing import Literal, Optional

from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.configuration.ai_model_configuration import (
    get_resolved_ai_model_configuration,
)


class OrganizationModelServicesContext(BaseModel):
    config_source: Literal["organization_v2", "legacy_user_v1", "empty"]
    has_model_configuration_v2: bool
    managed_service_version: Optional[int] = None
    uses_managed_service_v2: bool


class OrganizationContextResponse(BaseModel):
    organization_id: Optional[int] = None
    organization_provider_id: Optional[str] = None
    model_services: OrganizationModelServicesContext


async def get_organization_context(user: UserModel) -> OrganizationContextResponse:
    organization_id = user.selected_organization_id
    organization = (
        await db_client.get_organization_by_id(organization_id)
        if organization_id
        else None
    )

    resolved = await get_resolved_ai_model_configuration(
        user_id=user.id,
        organization_id=organization_id,
    )
    managed_service_version = resolved.effective.managed_service_version

    return OrganizationContextResponse(
        organization_id=organization_id,
        organization_provider_id=organization.provider_id if organization else None,
        model_services=OrganizationModelServicesContext(
            config_source=resolved.source,
            has_model_configuration_v2=resolved.source == "organization_v2",
            managed_service_version=managed_service_version,
            uses_managed_service_v2=(
                resolved.source == "organization_v2" and managed_service_version == 2
            ),
        ),
    )
