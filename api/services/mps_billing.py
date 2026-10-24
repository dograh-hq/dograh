from typing import Optional

from loguru import logger

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.mps_service_key_client import mps_service_key_client


async def ensure_hosted_mps_billing_account_v2(
    organization_id: int,
    *,
    created_by: Optional[str] = None,
) -> Optional[dict]:
    """Ensure hosted orgs have an MPS billing v2 account.

    OSS deployments use their configured service key's billing account (or an
    existing legacy per-key quota); local signup does not create an account.
    """
    if DEPLOYMENT_MODE == "oss":
        return None

    return await mps_service_key_client.ensure_billing_account_v2(
        organization_id=organization_id,
        created_by=created_by,
    )


_SIGNUP_GRANT_KEY = OrganizationConfigurationKey.MPS_SIGNUP_GRANT.value


async def ensure_signup_grant(organization_id: int, *, created_by: str) -> None:
    """Remember successful claims per organization across members and workers.

    MPS makes the grant idempotent, including concurrent first requests. A grant
    completes the team; a denial only completes that actor's attempt, since an
    eligible teammate can still claim. Dependency failures remain retryable
    without preventing authentication.
    """
    if DEPLOYMENT_MODE == "oss":
        return
    actor_key = f"{_SIGNUP_GRANT_KEY}:{created_by}"
    try:
        state = await db_client.get_configuration_values(
            organization_id, [_SIGNUP_GRANT_KEY, actor_key]
        )
        if state.get(_SIGNUP_GRANT_KEY) is True or state.get(actor_key) is True:
            return
        result = await mps_service_key_client.claim_signup_grant(
            organization_id=organization_id,
            created_by=created_by,
        )
        key = _SIGNUP_GRANT_KEY if result["granted"] is True else actor_key
        await db_client.upsert_configuration(organization_id, key, True)
    except Exception:
        logger.warning(
            "Signup grant unavailable for organization {}; will retry",
            organization_id,
        )
