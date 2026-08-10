"""Provision the Cloudonix telephony configuration created during org signup."""

from typing import Any

from loguru import logger

from api.db import db_client
from api.db.telephony_configuration_client import (
    TelephonyConfigurationConflictError,
)
from api.services.mps_service_key_client import mps_service_key_client

from . import _preprocess_credentials_on_save
from .config import normalize_cloudonix_domain

MANAGED_CONFIGURATION_NAME = "Dograh Cloudonix SIP"
MANAGED_BY = "dograh-mps"


def _managed_configuration(rows: list[Any]):
    """Return the MPS-managed row without treating user configs as managed."""
    for row in rows:
        credentials = row.credentials or {}
        if (
            row.name == MANAGED_CONFIGURATION_NAME
            and row.provider == "cloudonix"
            and credentials.get("managed_by") == MANAGED_BY
        ):
            return row
    return None


async def has_managed_cloudonix_configuration(organization_id: int) -> bool:
    """Report whether the organization already has MPS-managed SIP connectivity.

    Deliberately ignores rows the customer configured themselves: those carry
    their own Cloudonix credentials and are not ours to reprovision or replace.
    """
    rows = await db_client.list_telephony_configurations(organization_id)
    return _managed_configuration(rows) is not None


async def ensure_managed_cloudonix_configuration(
    organization_id: int,
    *,
    mps_organization_id: int | None,
    created_by: str,
):
    """Ensure one MPS-backed Cloudonix configuration for an organization.

    MPS owns domain creation and customer-token custody. The provider save
    hook remains responsible for deployment-specific resources such as the
    inbound Voice Application and its callback URL.
    """
    provisioning = await mps_service_key_client.ensure_cloudonix_domain(
        organization_id=mps_organization_id,
        created_by=created_by,
    )
    rows = await db_client.list_telephony_configurations(organization_id)
    existing = _managed_configuration(rows)
    domain_id = normalize_cloudonix_domain(provisioning["domain_name"])
    credentials = {
        **((existing.credentials or {}) if existing is not None else {}),
        "bearer_token": provisioning["bearer_token"],
        "domain_id": domain_id,
        "domain_uuid": provisioning["domain_uuid"],
        "managed_by": MANAGED_BY,
        "provisioning_id": provisioning["provisioning_id"],
    }
    credentials = await _preprocess_credentials_on_save(credentials)

    if existing is not None:
        if existing.credentials != credentials:
            return await db_client.update_telephony_configuration(
                config_id=existing.id,
                organization_id=organization_id,
                credentials=credentials,
            )
        return existing

    try:
        return await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name=MANAGED_CONFIGURATION_NAME,
            provider="cloudonix",
            credentials=credentials,
            is_default_outbound=False,
        )
    except TelephonyConfigurationConflictError:
        # A concurrent signup/bootstrap may have won the unique org/name race.
        rows = await db_client.list_telephony_configurations(organization_id)
        existing = _managed_configuration(rows)
        if existing is not None:
            return existing
        logger.warning(
            "Managed Cloudonix configuration name is already occupied by an "
            "unmanaged row for organization {}",
            organization_id,
        )
        raise


__all__ = [
    "MANAGED_BY",
    "MANAGED_CONFIGURATION_NAME",
    "ensure_managed_cloudonix_configuration",
    "has_managed_cloudonix_configuration",
]
