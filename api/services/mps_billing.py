from contextlib import suppress
from datetime import timedelta
from typing import Any, Optional

from loguru import logger

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.db.organization_configuration_client import LEASE_COMPLETED
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
# How long a claim left pending by a process that died mid-request blocks the
# next attempt. The claim itself is one MPS call.
SIGNUP_GRANT_LEASE_STALE_AFTER = timedelta(minutes=1)


def _actor_claim_done(value: Any) -> bool:
    # True was written before the per-actor key doubled as a lease.
    return value is True or (
        isinstance(value, dict) and value.get("status") == LEASE_COMPLETED
    )


async def ensure_signup_grant(organization_id: int, *, created_by: str) -> None:
    """Claim the hosted signup grant once per person and organization.

    Runs on every authenticated request until it succeeds, and a first page
    load sends several at once, so the per-actor key is a single-winner lease:
    only the request that takes it calls MPS, and the others skip the claim
    rather than repeat it. A grant completes the team; a denial only completes
    that actor's attempt, since an eligible teammate can still claim.
    Dependency failures release the lease, so the next request retries,
    without preventing authentication.
    """
    if DEPLOYMENT_MODE == "oss":
        return
    actor_key = f"{_SIGNUP_GRANT_KEY}:{created_by}"
    owner_token = None
    try:
        state = await db_client.get_configuration_values(
            organization_id, [_SIGNUP_GRANT_KEY, actor_key]
        )
        if state.get(_SIGNUP_GRANT_KEY) is True or _actor_claim_done(
            state.get(actor_key)
        ):
            return
        owner_token = await db_client.claim_configuration_lease(
            organization_id, actor_key, SIGNUP_GRANT_LEASE_STALE_AFTER
        )
        if owner_token is None:
            # Another request is claiming for this person right now.
            return
        result = await mps_service_key_client.claim_signup_grant(
            organization_id=organization_id,
            created_by=created_by,
        )
        if result["granted"] is True:
            await db_client.upsert_configuration(
                organization_id, _SIGNUP_GRANT_KEY, True
            )
        await db_client.complete_configuration_lease(
            organization_id, actor_key, owner_token
        )
    except Exception:
        logger.warning(
            "Signup grant unavailable for organization {}; will retry",
            organization_id,
        )
        if owner_token is not None:
            # If this fails too, the lease goes stale and is taken over.
            with suppress(Exception):
                await db_client.release_configuration_lease(
                    organization_id, actor_key, owner_token
                )
