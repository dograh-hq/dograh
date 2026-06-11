"""Superuser admin "Clients" endpoints.

Lists every client organization (excluding the superuser's own orgs) with
its VoiceLink provisioning state, supports retrying a failed provisioning
with a freshly supplied password (client passwords are never stored), and
assigns a DID by creating/updating the org's ``voicelink`` telephony
configuration row. All endpoints require superuser privileges.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.db import db_client
from api.db.models import OrganizationModel, UserModel
from api.schemas.admin_clients import (
    AdminClientItem,
    AdminClientsListResponse,
    AssignDidRequest,
    AssignDidResponse,
    RetryProvisionRequest,
    RetryProvisionResponse,
)
from api.services.auth.depends import get_superuser
from api.services.voicelink_clients import (
    get_voicelink_clients_client,
    provision_voicelink_client,
)
from api.services.voicelink_kyc.client import DEFAULT_VOICELINK_API_BASE

router = APIRouter(prefix="/admin/clients", tags=["admin-clients"])

VOICELINK_PROVIDER = "voicelink"


def _resolve_owner(organization: OrganizationModel) -> Optional[UserModel]:
    """The org owner: local signup creates orgs as ``org_<user.provider_id>``;
    fall back to the earliest member."""
    users: List[UserModel] = list(organization.users or [])
    if not users:
        return None
    for user in users:
        if f"org_{user.provider_id}" == organization.provider_id:
            return user
    return min(users, key=lambda u: u.id)


def _ordered_voicelink_configs(configs):
    """Default-outbound config first."""
    return sorted(configs, key=lambda c: not c.is_default_outbound)


@router.get("", response_model=AdminClientsListResponse)
async def list_clients(
    user: UserModel = Depends(get_superuser),
) -> AdminClientsListResponse:
    """All client organizations (the superuser's own orgs are excluded)."""
    organizations = await db_client.list_organizations_with_users(
        exclude_user_id=user.id
    )

    clients: List[AdminClientItem] = []
    for organization in organizations:
        owner = _resolve_owner(organization)
        configs = await db_client.list_telephony_configurations_by_provider(
            organization.id, VOICELINK_PROVIDER
        )
        did_number = next(
            (
                (config.credentials or {}).get("did_number")
                for config in _ordered_voicelink_configs(configs)
                if (config.credentials or {}).get("did_number")
            ),
            None,
        )
        clients.append(
            AdminClientItem(
                organization_id=organization.id,
                organization_name=organization.provider_id,
                owner_user_id=owner.id if owner else None,
                owner_email=owner.email if owner else None,
                owner_provider_id=owner.provider_id if owner else None,
                created_at=organization.created_at,
                voicelink_status=organization.voicelink_status,
                voicelink_client_id=organization.voicelink_client_id,
                voicelink_username=organization.voicelink_username,
                voicelink_error=organization.voicelink_error,
                has_voicelink_config=bool(configs),
                did_number=did_number,
            )
        )

    return AdminClientsListResponse(clients=clients)


@router.post("/{org_id}/retry-provision", response_model=RetryProvisionResponse)
async def retry_provision(
    org_id: int,
    request: RetryProvisionRequest,
    user: UserModel = Depends(get_superuser),
) -> RetryProvisionResponse:
    """Re-run VoiceLink client creation for an org.

    Uses the stored ``voicelink_username`` (or re-derives one) and the NEW
    password supplied in the body — passwords are never stored locally.
    """
    organization = await db_client.get_organization_with_users(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    client = get_voicelink_clients_client()
    if not client.is_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "VoiceLink reseller credentials are not configured — set "
                "VOICELINK_RESELLER_USERNAME and VOICELINK_RESELLER_PASSWORD"
            ),
        )

    owner = _resolve_owner(organization)
    if owner is None or not owner.email:
        raise HTTPException(
            status_code=400,
            detail="Organization has no member user with an email address",
        )

    result = await provision_voicelink_client(
        organization.id,
        email=owner.email,
        password=request.password,
        username=organization.voicelink_username or None,
        client=client,
    )
    logger.info(
        f"Superuser {user.id} retried VoiceLink provisioning for org {org_id}: "
        f"{result['status']}"
    )
    return RetryProvisionResponse(
        voicelink_status=result["status"],
        voicelink_client_id=result["client_id"],
        voicelink_username=result["username"],
        voicelink_error=result["error"],
    )


@router.post("/{org_id}/assign-did", response_model=AssignDidResponse)
async def assign_did(
    org_id: int,
    request: AssignDidRequest,
    user: UserModel = Depends(get_superuser),
) -> AssignDidResponse:
    """Create/update the org's ``voicelink`` telephony configuration with a DID.

    The row is org-scoped and marked default for outbound, so the client can
    dial as soon as the owner maps the DID + channels in the VoiceLink portal.
    """
    organization = await db_client.get_organization_by_id(org_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    client_id = request.client_id or organization.voicelink_client_id

    configs = await db_client.list_telephony_configurations_by_provider(
        org_id, VOICELINK_PROVIDER
    )

    if configs:
        target = _ordered_voicelink_configs(configs)[0]
        credentials = dict(target.credentials or {})
        credentials["did_number"] = request.did_number
        credentials.setdefault("api_base", DEFAULT_VOICELINK_API_BASE)
        if client_id:
            credentials["client_id"] = str(client_id)
        updated = await db_client.update_telephony_configuration(
            target.id, org_id, credentials=credentials
        )
        if updated is None:
            raise HTTPException(
                status_code=404, detail="Telephony configuration not found"
            )
        if not updated.is_default_outbound:
            await db_client.set_default_telephony_configuration(updated.id, org_id)
        configuration_id = updated.id
        created = False
    else:
        credentials = {
            "api_base": DEFAULT_VOICELINK_API_BASE,
            "did_number": request.did_number,
        }
        if organization.voicelink_username:
            credentials["username"] = organization.voicelink_username
        if client_id:
            credentials["client_id"] = str(client_id)
        row = await db_client.create_telephony_configuration(
            organization_id=org_id,
            name="VoiceLink",
            provider=VOICELINK_PROVIDER,
            credentials=credentials,
            is_default_outbound=True,
        )
        configuration_id = row.id
        created = True

    logger.info(
        f"Superuser {user.id} assigned DID to org {org_id} "
        f"(configuration_id={configuration_id}, created={created})"
    )
    return AssignDidResponse(
        configuration_id=configuration_id,
        created=created,
        did_number=request.did_number,
        client_id=str(client_id) if client_id else None,
    )
