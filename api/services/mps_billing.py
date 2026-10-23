from typing import Optional

from loguru import logger

from api.constants import DEPLOYMENT_MODE
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


# Cache only successful, idempotent claims. A failed dependency call is retried
# on the next authenticated request; it never prevents signup or authentication.
_signup_grants: set[tuple[int, str]] = set()


async def ensure_signup_grant(organization_id: int, *, created_by: str) -> None:
    if DEPLOYMENT_MODE == "oss" or (organization_id, created_by) in _signup_grants:
        return
    try:
        await mps_service_key_client.claim_signup_grant(
            organization_id=organization_id,
            created_by=created_by,
        )
    except Exception:
        logger.warning(
            "Signup grant unavailable for organization {}; will retry",
            organization_id,
        )
        return
    if len(_signup_grants) >= 10000:
        _signup_grants.clear()
    _signup_grants.add((organization_id, created_by))
