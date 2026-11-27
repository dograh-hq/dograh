"""Connect a self-hosted organization to a Dograh account.

The install starts a request at MPS, shows the person a code, and polls until
they approve it on the hosted app. The first poll after approval carries a
service key on the approving account; it goes straight into the
organization's Dograh connection, and never reaches the browser.
"""

import asyncio
import hashlib
from urllib.parse import urlsplit

from api.db import db_client
from api.errors.mps import MPSUnavailableError
from api.schemas.dograh_account_connect import (
    AccountConnectPoll,
    AccountConnectStart,
    DograhAccountStatus,
)
from api.services.configuration.model_connections import (
    dograh_key_connection_uuids,
    dograh_model_configuration_spec,
)
from api.services.mps_service_key_client import mps_service_key_client

_INSTALL_NAME_LIMIT = 80


async def get_dograh_account_status(organization_id: int) -> DograhAccountStatus:
    """Whether the organization has a Dograh key that MPS still accepts.

    Checks the same key the agent builder and other Dograh features run on.
    When MPS cannot be reached the key is reported as connected: offering to
    connect again would not help, and the feature reports the outage itself.
    """
    service_key = await mps_service_key_client.find_runtime_service_key(organization_id)
    if not service_key:
        return DograhAccountStatus(status="missing")
    try:
        accepted = await asyncio.to_thread(
            mps_service_key_client.validate_service_key,
            service_key,
            organization_id=organization_id,
        )
    except MPSUnavailableError:
        accepted = True
    return DograhAccountStatus(status="connected" if accepted else "invalid")


def install_name_for(install_url: str | None) -> str:
    """How the approval page names this install: the host people reach it on."""
    host = urlsplit(install_url or "").netloc
    return (host or "Self-hosted Dograh")[:_INSTALL_NAME_LIMIT]


async def start_account_connect(
    *, install_url: str | None, created_by: str
) -> AccountConnectStart:
    started = await mps_service_key_client.start_account_connect(
        install_name=install_name_for(install_url),
        install_url=install_url,
        created_by=created_by,
    )
    return AccountConnectStart.model_validate(started)


async def poll_account_connect(
    organization_id: int, device_code: str
) -> AccountConnectPoll:
    # Save a receipt in the same transaction as the credentials. A lost reply
    # (including a lost MPS acknowledgement) must not repeat a catalog mutation.
    receipt_key = (
        "dograh_account_connect:" + hashlib.sha256(device_code.encode()).hexdigest()
    )
    state = await db_client.get_configuration_values(organization_id, [receipt_key])
    if receipt_key in state:
        await mps_service_key_client.acknowledge_account_connect(device_code)
        return AccountConnectPoll.model_validate(state[receipt_key])

    result = await mps_service_key_client.poll_account_connect(device_code)
    status = result.get("status")
    if status != "approved":
        known = {"pending", "denied", "expired", "collected"}
        return AccountConnectPoll(status=status if status in known else "pending")

    saved = await db_client.store_connected_service_key(
        organization_id,
        receipt_key=receipt_key,
        service_key=result["service_key"],
        team_name=result.get("team_name"),
        key_prefix=result.get("key_prefix"),
        configuration_for=dograh_model_configuration_spec,
        key_connections_for=dograh_key_connection_uuids,
    )
    await mps_service_key_client.acknowledge_account_connect(device_code)
    return AccountConnectPoll.model_validate(saved)
