"""Cloudonix telephony provider package."""

import uuid
from typing import Any
from urllib.parse import quote

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)
from api.utils.common import get_backend_endpoints

from .config import CloudonixConfigurationRequest, CloudonixConfigurationResponse
from .provider import CLOUDONIX_API_BASE_URL, CloudonixProvider
from .regions import CLOUDONIX_REGION_NAMES, get_cloudonix_region
from .transport import create_transport


def _config_loader(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "cloudonix",
        "bearer_token": value.get("bearer_token"),
        "api_key": value.get("api_key"),  # For x-cx-apikey validation
        "domain_id": value.get("domain_id"),
        "domain_uuid": value.get("domain_uuid"),
        "application_name": value.get("application_name"),
        "application_id": value.get("application_id"),
        "application_uuid": value.get("application_uuid"),
        "outbound_trunks": value.get("outbound_trunks") or [],
        "outbound_trunk_uuids": value.get("outbound_trunk_uuids") or {},
        "from_numbers": value.get("from_numbers", []),
    }


async def _fetch_domain_uuid(credentials: dict[str, Any]) -> dict[str, Any]:
    """Resolve and store the immutable UUID for the configured domain.

    ``domain_id`` is the human-readable Cloudonix domain name used by the
    existing API calls. SIP hostnames require the separate ``uuid`` returned
    by Cloudonix's ``domainGet`` operation. The save route strips client input
    for this server-managed field and carries the stored value forward, so a
    lookup is only needed when no UUID has been persisted yet.
    """
    domain_uuid = credentials.get("domain_uuid")
    if isinstance(domain_uuid, str) and domain_uuid.strip():
        return credentials

    bearer_token = credentials.get("bearer_token")
    domain_id = credentials.get("domain_id")
    if not bearer_token or not domain_id:
        return credentials

    encoded_domain_id = quote(str(domain_id), safe="")
    endpoint = f"{CLOUDONIX_API_BASE_URL}/customers/self/domains/{encoded_domain_id}"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Accept": "application/json",
    }

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(endpoint, headers=headers) as response,
        ):
            response_text = await response.text()
            if response.status != 200:
                logger.error(
                    f"[Cloudonix] domainGet failed for {domain_id}: "
                    f"HTTP {response.status} body={response_text}"
                )
                raise HTTPException(
                    status_code=response.status,
                    detail=(
                        f"Failed to fetch Cloudonix domain UUID: HTTP {response.status}"
                    ),
                )
            try:
                data = await response.json()
            except ValueError as e:
                logger.error(
                    f"[Cloudonix] domainGet returned invalid JSON for {domain_id}: {e}"
                )
                raise HTTPException(
                    status_code=502,
                    detail="Cloudonix domainGet returned an invalid response",
                ) from e
    except aiohttp.ClientError as e:
        logger.error(f"[Cloudonix] domainGet transport error for {domain_id}: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach Cloudonix to fetch the domain UUID: {e}",
        ) from e

    domain_uuid = data.get("uuid") if isinstance(data, dict) else None
    if not isinstance(domain_uuid, str) or not domain_uuid.strip():
        logger.error(
            f"[Cloudonix] domainGet response missing uuid for domain {domain_id}"
        )
        raise HTTPException(
            status_code=502,
            detail="Cloudonix domainGet response did not include a domain UUID",
        )

    return {**credentials, "domain_uuid": domain_uuid.strip()}


async def _ensure_application_name(credentials: dict[str, Any]) -> dict[str, Any]:
    """Create/recover a Voice Application and make it the domain default.

    The application is created with our inbound dispatcher URL pre-set — the
    same URL ``configure_inbound`` would PATCH later — so inbound calls work
    immediately for any DNID bound to this application. MPS-managed configs
    use a deterministic name so a retry after a partial failure discovers the
    existing application instead of creating another one.
    """
    if credentials.get("application_name"):
        return credentials

    bearer_token = credentials.get("bearer_token")
    domain_id = credentials.get("domain_id")
    if not bearer_token or not domain_id:
        return credentials

    backend_endpoint, _ = await get_backend_endpoints()
    inbound_url = f"{backend_endpoint}/api/v1/telephony/inbound/run"

    provisioning_id = credentials.get("provisioning_id")
    if isinstance(provisioning_id, str) and provisioning_id.strip():
        stable_id = "".join(ch for ch in provisioning_id.lower() if ch.isalnum())
        name = f"dograh-{stable_id[:24]}"
    else:
        name = f"dograh-{uuid.uuid4().hex[:12]}"

    encoded_domain_id = quote(str(domain_id), safe="")
    endpoint = (
        f"{CLOUDONIX_API_BASE_URL}/customers/self/domains/"
        f"{encoded_domain_id}/applications"
    )
    body = {"name": name, "type": "cxml", "url": inbound_url, "method": "POST"}
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            data: dict[str, Any] | None = None

            # Only managed applications have a deterministic name worth
            # recovering. Manual blank-name saves retain the existing create
            # behavior and receive a fresh random name.
            if provisioning_id:
                async with session.get(endpoint, headers=headers) as response:
                    await response.text()
                    if response.status == 200:
                        listed = await response.json()
                        applications = (
                            listed
                            if isinstance(listed, list)
                            else listed.get("applications", [])
                            if isinstance(listed, dict)
                            else []
                        )
                        data = next(
                            (
                                app
                                for app in applications
                                if isinstance(app, dict) and app.get("name") == name
                            ),
                            None,
                        )
                    elif response.status != 404:
                        raise HTTPException(
                            status_code=response.status,
                            detail=(
                                "Failed to list Cloudonix Voice Applications: "
                                f"HTTP {response.status}"
                            ),
                        )

            if data is None:
                async with session.post(
                    endpoint, json=body, headers=headers
                ) as response:
                    await response.text()
                    if response.status not in (200, 201):
                        logger.error(
                            "[Cloudonix] applicationCreate failed: HTTP {}",
                            response.status,
                        )
                        raise HTTPException(
                            status_code=response.status,
                            detail=(
                                "Failed to auto-create Cloudonix Voice "
                                f"Application: HTTP {response.status}"
                            ),
                        )
                    data = await response.json()

            application_id = data.get("id") if isinstance(data, dict) else None
            if not isinstance(application_id, int):
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Cloudonix application response did not include a numeric ID"
                    ),
                )

            domain_endpoint = (
                f"{CLOUDONIX_API_BASE_URL}/customers/self/domains/{encoded_domain_id}"
            )
            async with session.put(
                domain_endpoint,
                json={"defaultApplication": application_id},
                headers=headers,
            ) as response:
                await response.text()
                if response.status not in (200, 204):
                    raise HTTPException(
                        status_code=response.status,
                        detail=(
                            "Failed to set the Cloudonix default application: "
                            f"HTTP {response.status}"
                        ),
                    )
    except aiohttp.ClientError as e:
        logger.error(f"[Cloudonix] applicationCreate transport error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach Cloudonix to auto-create application: {e}",
        )

    created_name = data.get("name") or name
    logger.info(
        f"[Cloudonix] ensured Voice Application '{created_name}' on domain {domain_id}"
    )
    updated = {
        **credentials,
        "application_name": created_name,
        "application_id": application_id,
    }
    application_uuid = data.get("uuid")
    if isinstance(application_uuid, str) and application_uuid:
        updated["application_uuid"] = application_uuid
    return updated


_OUTBOUND_TRUNK_DIRECTION = "public-outbound"
_MANAGED_TRUNK_PROFILE_FIELDS = {
    "hostname",
    "domain",
    "ruri-domain",
    "connection-timeout",
    "provisional-timeout",
    "authentication",
}


def _build_outbound_trunk_profile(configuration: dict[str, Any]) -> dict[str, Any]:
    """One operator-supplied SIP domain drives both Cloudonix domain keys."""
    sip_domain = configuration.get("sip_domain")
    if not isinstance(sip_domain, str) or not sip_domain.strip():
        return {}

    sip_domain = sip_domain.strip()
    return {"domain": sip_domain, "ruri-domain": sip_domain}


def _build_outbound_trunk_payload(
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Expand the two operator fields into a full Cloudonix trunk payload.

    The remote peer comes from the configured region rather than the operator:
    a Dograh-managed trunk always terminates on that region's Cloudonix edge.
    """
    region = get_cloudonix_region(configuration.get("region"))
    if region is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Outbound trunk is missing a known Cloudonix region; expected "
                "one of: " + ", ".join(CLOUDONIX_REGION_NAMES)
            ),
        )

    payload: dict[str, Any] = {
        "name": configuration["name"],
        "ip": region.edge_ip,
        "port": region.sip_port,
        "transport": "udp",
        "prefix": "",
        "direction": _OUTBOUND_TRUNK_DIRECTION,
    }
    profile = _build_outbound_trunk_profile(configuration)
    if profile:
        payload["profile"] = profile
    return payload


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a safe-to-log copy of the request headers."""
    return {**headers, "Authorization": "Bearer [REDACTED]"}


def _redact_outbound_trunk_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a safe-to-log copy without the remote SIP password."""
    safe_payload = dict(payload)
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return safe_payload

    safe_profile = dict(profile)
    authentication = profile.get("authentication")
    if isinstance(authentication, dict):
        safe_profile["authentication"] = {
            **authentication,
            "password": "[REDACTED]",
        }
    safe_payload["profile"] = safe_profile
    return safe_payload


async def _list_outbound_domain_trunks(
    session: aiohttp.ClientSession,
    endpoint: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    async with session.get(endpoint, headers=headers) as response:
        await response.text()
        if response.status == 404:
            return []
        if response.status != 200:
            logger.error(f"[Cloudonix] trunksList failed: HTTP {response.status}")
            raise HTTPException(
                status_code=response.status,
                detail=f"Failed to list Cloudonix voice trunks: HTTP {response.status}",
            )
        try:
            data = await response.json()
        except (aiohttp.ContentTypeError, ValueError) as e:
            logger.error(f"[Cloudonix] trunksList returned invalid JSON: {e}")
            raise HTTPException(
                status_code=502,
                detail="Cloudonix trunksList returned an invalid response",
            ) from e

    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        logger.error("[Cloudonix] trunksList response was not an array of objects")
        raise HTTPException(
            status_code=502,
            detail="Cloudonix trunksList returned an invalid response",
        )
    return data


def _find_managed_outbound_trunk(
    trunks: list[dict[str, Any]],
    trunk_uuid: str | None,
    trunk_name: str | None,
) -> dict[str, Any] | None:
    if trunk_uuid:
        for trunk in trunks:
            if trunk.get("uuid") == trunk_uuid:
                return trunk

    if trunk_name:
        for trunk in trunks:
            if (
                trunk.get("name") == trunk_name
                and trunk.get("direction") == _OUTBOUND_TRUNK_DIRECTION
            ):
                return trunk
    return None


def _outbound_trunk_update_payload(
    desired: dict[str, Any], existing: dict[str, Any]
) -> dict[str, Any]:
    """Build a full update while retaining Cockpit-managed profile metadata."""
    existing_profile = existing.get("profile")
    unknown_profile = (
        {
            key: value
            for key, value in existing_profile.items()
            if key not in _MANAGED_TRUNK_PROFILE_FIELDS
        }
        if isinstance(existing_profile, dict)
        else {}
    )
    desired_profile = desired.get("profile")
    profile = {
        **unknown_profile,
        **(desired_profile if isinstance(desired_profile, dict) else {}),
    }

    payload = {**desired, "active": True}
    if profile or isinstance(existing_profile, dict):
        payload["profile"] = profile
    return payload


def _outbound_trunk_needs_update(
    existing: dict[str, Any], update_payload: dict[str, Any]
) -> bool:
    for field in ("name", "ip", "transport", "prefix", "direction", "active"):
        if existing.get(field) != update_payload.get(field):
            return True

    try:
        existing_port = int(existing.get("port"))
        desired_port = int(update_payload.get("port"))
    except (TypeError, ValueError):
        return True
    if existing_port != desired_port:
        return True

    existing_profile = existing.get("profile")
    normalized_existing_profile = (
        existing_profile if isinstance(existing_profile, dict) else {}
    )
    desired_profile = update_payload.get("profile")
    normalized_desired_profile = (
        desired_profile if isinstance(desired_profile, dict) else {}
    )
    return normalized_existing_profile != normalized_desired_profile


async def _deactivate_outbound_trunk(
    session,
    collection_endpoint: str,
    headers: dict[str, str],
    remote: dict[str, Any],
) -> None:
    """Mark an existing Cloudonix trunk inactive. No-op when already inactive."""
    if remote.get("active") is False:
        return

    remote_uuid = remote.get("uuid")
    if not isinstance(remote_uuid, str) or not remote_uuid:
        raise HTTPException(
            status_code=502,
            detail="Cloudonix voice trunk response did not include a UUID",
        )

    endpoint = f"{collection_endpoint}/{quote(remote_uuid, safe='')}"
    body = {"active": False}
    logger.info(
        f"[Cloudonix] trunkUpdate request:\n"
        f"Method: PUT\nEndpoint: {endpoint}\n"
        f"Headers: {_redact_headers(headers)}\nPayload: {body}"
    )
    async with session.put(endpoint, json=body, headers=headers) as response:
        await response.text()
        if response.status not in (200, 204):
            logger.error(
                f"[Cloudonix] trunkUpdate deactivation failed: HTTP {response.status}"
            )
            raise HTTPException(
                status_code=response.status,
                detail=(
                    "Failed to deactivate Cloudonix outbound trunk: "
                    f"HTTP {response.status}"
                ),
            )


async def _apply_outbound_trunk(
    session,
    collection_endpoint: str,
    headers: dict[str, str],
    remote_trunks: list[dict[str, Any]],
    configuration: dict[str, Any],
    remote_uuid: str | None,
) -> str:
    """Create or update one trunk on Cloudonix. Returns its Cloudonix UUID."""
    trunk_name = configuration.get("name")
    trunk_name = trunk_name if isinstance(trunk_name, str) else None
    existing = _find_managed_outbound_trunk(remote_trunks, remote_uuid, trunk_name)

    same_name = next(
        (
            trunk
            for trunk in remote_trunks
            if trunk.get("name") == trunk_name
            and trunk.get("direction") != _OUTBOUND_TRUNK_DIRECTION
        ),
        None,
    )
    if existing is None and same_name is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A Cloudonix trunk named '{trunk_name}' already exists "
                "with a different direction"
            ),
        )

    desired = _build_outbound_trunk_payload(configuration)
    if existing is not None:
        existing_uuid = existing.get("uuid")
        if not isinstance(existing_uuid, str) or not existing_uuid:
            raise HTTPException(
                status_code=502,
                detail="Cloudonix voice trunk response did not include a UUID",
            )
        update_payload = _outbound_trunk_update_payload(desired, existing)
        if _outbound_trunk_needs_update(existing, update_payload):
            endpoint = f"{collection_endpoint}/{quote(existing_uuid, safe='')}"
            logger.info(
                f"[Cloudonix] trunkUpdate request:\n"
                f"Method: PUT\nEndpoint: {endpoint}\n"
                f"Headers: {_redact_headers(headers)}\n"
                f"Payload: {_redact_outbound_trunk_payload(update_payload)}"
            )
            async with session.put(
                endpoint, json=update_payload, headers=headers
            ) as response:
                await response.text()
                if response.status not in (200, 204):
                    logger.error(
                        f"[Cloudonix] trunkUpdate failed: HTTP {response.status}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=(
                            "Failed to update Cloudonix outbound trunk: "
                            f"HTTP {response.status}"
                        ),
                    )
        return existing_uuid

    logger.info(
        f"[Cloudonix] trunkCreate request:\n"
        f"Method: POST\nEndpoint: {collection_endpoint}\n"
        f"Headers: {_redact_headers(headers)}\n"
        f"Payload: {_redact_outbound_trunk_payload(desired)}"
    )
    async with session.post(
        collection_endpoint, json=desired, headers=headers
    ) as response:
        await response.text()
        if response.status not in (200, 201):
            logger.error(f"[Cloudonix] trunkCreate failed: HTTP {response.status}")
            raise HTTPException(
                status_code=response.status,
                detail=(
                    f"Failed to create Cloudonix outbound trunk: HTTP {response.status}"
                ),
            )
        try:
            created = await response.json()
        except (aiohttp.ContentTypeError, ValueError):
            created = None

    created_uuid = created.get("uuid") if isinstance(created, dict) else None
    if not isinstance(created_uuid, str) or not created_uuid:
        # Some Cloudonix deployments return an empty success response. Resolve
        # the newly-created resource by its unique name instead.
        refreshed = await _list_outbound_domain_trunks(
            session, collection_endpoint, headers
        )
        created_trunk = _find_managed_outbound_trunk(refreshed, None, trunk_name)
        created_uuid = (
            created_trunk.get("uuid") if isinstance(created_trunk, dict) else None
        )
    if not isinstance(created_uuid, str) or not created_uuid:
        raise HTTPException(
            status_code=502,
            detail="Cloudonix trunkCreate response did not include a UUID",
        )

    logger.info(f"[Cloudonix] created outbound voice trunk '{trunk_name}'")
    return created_uuid


async def _ensure_outbound_trunks(credentials: dict[str, Any]) -> dict[str, Any]:
    """Reconcile every configured outbound trunk against Cloudonix.

    ``outbound_trunks`` is the desired state and ``outbound_trunk_uuids`` maps
    each Dograh trunk id to the Cloudonix UUID it provisioned. Keeping the map
    keyed by our own id — rather than matching on trunk name — means a rename
    updates the existing Cloudonix trunk instead of orphaning it. A trunk that
    is disabled or dropped from the list is deactivated remotely.
    """
    configurations = credentials.get("outbound_trunks")
    configurations = configurations if isinstance(configurations, list) else []
    stored_uuids = credentials.get("outbound_trunk_uuids")
    stored_uuids = dict(stored_uuids) if isinstance(stored_uuids, dict) else {}

    if not configurations and not stored_uuids:
        return credentials

    bearer_token = credentials.get("bearer_token")
    domain_id = credentials.get("domain_id")
    if not bearer_token or not domain_id:
        return credentials

    encoded_domain_id = quote(str(domain_id), safe="")
    collection_endpoint = f"{CLOUDONIX_API_BASE_URL}/domains/{encoded_domain_id}/trunks"
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            remote_trunks = await _list_outbound_domain_trunks(
                session, collection_endpoint, headers
            )

            provisioned: dict[str, str] = {}
            for configuration in configurations:
                if not isinstance(configuration, dict):
                    continue
                trunk_id = configuration.get("id")
                if not isinstance(trunk_id, str) or not trunk_id:
                    raise HTTPException(
                        status_code=500,
                        detail="Outbound trunk is missing its Dograh identifier",
                    )
                if not configuration.get("enabled"):
                    continue
                provisioned[trunk_id] = await _apply_outbound_trunk(
                    session,
                    collection_endpoint,
                    headers,
                    remote_trunks,
                    configuration,
                    stored_uuids.get(trunk_id),
                )

            # Anything previously provisioned that is now disabled or removed.
            listed_ids = {
                configuration.get("id")
                for configuration in configurations
                if isinstance(configuration, dict)
            }
            resolved = dict(provisioned)
            for stale_id, stale_uuid in stored_uuids.items():
                if stale_id in provisioned:
                    continue
                remote = _find_managed_outbound_trunk(remote_trunks, stale_uuid, None)
                if remote is not None:
                    await _deactivate_outbound_trunk(
                        session, collection_endpoint, headers, remote
                    )
                # A trunk still on the config is only switched off: keep its
                # UUID so re-enabling reuses the Cloudonix trunk even if the
                # operator renamed it while it was disabled. A trunk dropped
                # from the config is forgotten.
                if stale_id in listed_ids:
                    resolved[stale_id] = stale_uuid

            return {**credentials, "outbound_trunk_uuids": resolved}
    except aiohttp.ClientError as e:
        logger.error(f"[Cloudonix] outbound trunk transport error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach Cloudonix to configure outbound trunk: {e}",
        ) from e


def _assign_outbound_trunk_ids(credentials: dict[str, Any]) -> dict[str, Any]:
    """Mint a Dograh id for any trunk saved without one."""
    configurations = credentials.get("outbound_trunks")
    if not isinstance(configurations, list) or not configurations:
        return credentials

    stamped = []
    for configuration in configurations:
        if isinstance(configuration, dict) and not configuration.get("id"):
            configuration = {**configuration, "id": str(uuid.uuid4())}
        stamped.append(configuration)
    return {**credentials, "outbound_trunks": stamped}


async def _preprocess_credentials_on_save(
    credentials: dict[str, Any],
) -> dict[str, Any]:
    credentials = await _fetch_domain_uuid(credentials)
    credentials = await _ensure_application_name(credentials)
    credentials = _assign_outbound_trunk_ids(credentials)
    return await _ensure_outbound_trunks(credentials)


_UI_METADATA = ProviderUIMetadata(
    display_name="Cloudonix",
    docs_url="https://docs.dograh.com/integrations/telephony/cloudonix",
    fields=[
        ProviderUIField(
            name="bearer_token",
            label="Bearer Token",
            type="password",
            sensitive=True,
            description="Cloudonix API Bearer Token",
        ),
        ProviderUIField(
            name="domain_id",
            label="Domain Name",
            type="text",
            description=(
                "Your Cloudonix domain (for example, acme.cloudonix.net). "
                "Dograh fetches and stores its UUID automatically."
            ),
        ),
        ProviderUIField(
            name="application_name",
            label="Application Name",
            type="text",
            required=False,
            description=(
                "Cloudonix Voice Application name whose url is updated when "
                "inbound workflows are attached to numbers on this domain. "
                "Leave blank and we will auto-create one for you on save."
            ),
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
        ),
    ],
)


SPEC = ProviderSpec(
    name="cloudonix",
    provider_cls=CloudonixProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=CloudonixConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=CloudonixConfigurationResponse,
    account_id_credential_field="domain_id",
    server_managed_credential_fields=(
        "domain_uuid",
        "application_id",
        "application_uuid",
        "managed_by",
        "provisioning_id",
        "outbound_trunk_uuids",
    ),
    account_scoped_server_managed_credential_fields=(
        "domain_uuid",
        "application_id",
        "application_uuid",
        "outbound_trunk_uuids",
    ),
    preprocess_credentials_on_save=_preprocess_credentials_on_save,
)


register(SPEC)


__all__ = [
    "SPEC",
    "CloudonixConfigurationRequest",
    "CloudonixConfigurationResponse",
    "CloudonixProvider",
    "create_transport",
]
