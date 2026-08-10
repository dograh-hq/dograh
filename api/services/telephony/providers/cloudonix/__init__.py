"""Cloudonix telephony provider package."""

import uuid
from typing import Any
from urllib.parse import quote

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUICondition,
    ProviderUIField,
    ProviderUIMetadata,
    ProviderUIOption,
    register,
)
from api.utils.common import get_backend_endpoints

from .config import CloudonixConfigurationRequest, CloudonixConfigurationResponse
from .provider import CLOUDONIX_API_BASE_URL, CloudonixProvider
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
        "outbound_trunk": value.get("outbound_trunk"),
        "outbound_trunk_uuid": value.get("outbound_trunk_uuid"),
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
    """Translate Dograh's Python-safe field names to Cloudonix profile keys."""
    configured_profile = configuration.get("profile")
    if not isinstance(configured_profile, dict):
        return {}

    profile: dict[str, Any] = {}
    profile_fields = {
        "hostname": "hostname",
        "domain": "domain",
        "ruri_domain": "ruri-domain",
        "connection_timeout": "connection-timeout",
        "provisional_timeout": "provisional-timeout",
    }
    for stored_name, cloudonix_name in profile_fields.items():
        value = configured_profile.get(stored_name)
        if value is not None and value != "":
            profile[cloudonix_name] = value

    authentication = configured_profile.get("authentication")
    if isinstance(authentication, dict):
        username = authentication.get("username")
        password = authentication.get("password")
        if username and password:
            profile["authentication"] = {
                "username": username,
                "password": password,
                "overwrite-from": bool(authentication.get("overwrite_from", False)),
            }

    return profile


def _build_outbound_trunk_payload(
    configuration: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": configuration["name"],
        "ip": configuration["ip"],
        "port": configuration.get("port", 5060),
        "transport": configuration.get("transport", "udp"),
        "prefix": configuration.get("prefix", ""),
        "direction": _OUTBOUND_TRUNK_DIRECTION,
    }
    profile = _build_outbound_trunk_profile(configuration)
    if profile:
        payload["profile"] = profile
    return payload


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


async def _ensure_outbound_trunk(credentials: dict[str, Any]) -> dict[str, Any]:
    """Create, update, or deactivate the Dograh-managed outbound trunk.

    Cloudonix trunk names are unique, and the created trunk UUID is persisted as
    a server-managed credential. Subsequent saves first match that UUID and then
    fall back to the configured name, preventing duplicate trunk creation if an
    older Dograh configuration predates UUID persistence.
    """
    configuration = credentials.get("outbound_trunk")
    if not isinstance(configuration, dict):
        return credentials

    enabled = bool(configuration.get("enabled"))
    trunk_uuid_value = credentials.get("outbound_trunk_uuid")
    trunk_uuid = (
        trunk_uuid_value.strip()
        if isinstance(trunk_uuid_value, str) and trunk_uuid_value.strip()
        else None
    )
    if not enabled and not trunk_uuid:
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
    safe_headers = {**headers, "Authorization": "Bearer [REDACTED]"}

    try:
        async with aiohttp.ClientSession() as session:
            trunks = await _list_outbound_domain_trunks(
                session, collection_endpoint, headers
            )
            trunk_name = configuration.get("name")
            existing = _find_managed_outbound_trunk(
                trunks,
                trunk_uuid,
                trunk_name if isinstance(trunk_name, str) else None,
            )

            if not enabled:
                if existing is None:
                    updated = dict(credentials)
                    updated.pop("outbound_trunk_uuid", None)
                    return updated
                if existing.get("active") is False:
                    return credentials

                existing_uuid = existing.get("uuid")
                if not isinstance(existing_uuid, str) or not existing_uuid:
                    raise HTTPException(
                        status_code=502,
                        detail="Cloudonix voice trunk response did not include a UUID",
                    )
                endpoint = f"{collection_endpoint}/{quote(existing_uuid, safe='')}"
                body = {"active": False}
                logger.info(
                    f"[Cloudonix] trunkUpdate request:\n"
                    f"Method: PUT\nEndpoint: {endpoint}\n"
                    f"Headers: {safe_headers}\nPayload: {body}"
                )
                async with session.put(
                    endpoint, json=body, headers=headers
                ) as response:
                    await response.text()
                    if response.status not in (200, 204):
                        logger.error(
                            f"[Cloudonix] trunkUpdate deactivation failed: "
                            f"HTTP {response.status}"
                        )
                        raise HTTPException(
                            status_code=response.status,
                            detail=(
                                "Failed to deactivate Cloudonix outbound trunk: "
                                f"HTTP {response.status}"
                            ),
                        )
                return credentials

            same_name = next(
                (
                    trunk
                    for trunk in trunks
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
                        f"Headers: {safe_headers}\n"
                        f"Payload: {_redact_outbound_trunk_payload(update_payload)}"
                    )
                    async with session.put(
                        endpoint, json=update_payload, headers=headers
                    ) as response:
                        await response.text()
                        if response.status not in (200, 204):
                            logger.error(
                                f"[Cloudonix] trunkUpdate failed: "
                                f"HTTP {response.status}"
                            )
                            raise HTTPException(
                                status_code=response.status,
                                detail=(
                                    "Failed to update Cloudonix outbound trunk: "
                                    f"HTTP {response.status}"
                                ),
                            )
                return {**credentials, "outbound_trunk_uuid": existing_uuid}

            logger.info(
                f"[Cloudonix] trunkCreate request:\n"
                f"Method: POST\nEndpoint: {collection_endpoint}\n"
                f"Headers: {safe_headers}\n"
                f"Payload: {_redact_outbound_trunk_payload(desired)}"
            )
            async with session.post(
                collection_endpoint, json=desired, headers=headers
            ) as response:
                await response.text()
                if response.status not in (200, 201):
                    logger.error(
                        f"[Cloudonix] trunkCreate failed: HTTP {response.status}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=(
                            "Failed to create Cloudonix outbound trunk: "
                            f"HTTP {response.status}"
                        ),
                    )
                try:
                    created = await response.json()
                except (aiohttp.ContentTypeError, ValueError):
                    created = None

            created_uuid = created.get("uuid") if isinstance(created, dict) else None
            if not isinstance(created_uuid, str) or not created_uuid:
                # Some Cloudonix deployments return an empty success response.
                # Resolve the newly-created resource by its unique name instead.
                trunks = await _list_outbound_domain_trunks(
                    session, collection_endpoint, headers
                )
                created_trunk = _find_managed_outbound_trunk(
                    trunks, None, str(trunk_name)
                )
                created_uuid = (
                    created_trunk.get("uuid")
                    if isinstance(created_trunk, dict)
                    else None
                )
            if not isinstance(created_uuid, str) or not created_uuid:
                raise HTTPException(
                    status_code=502,
                    detail="Cloudonix trunkCreate response did not include a UUID",
                )

            logger.info(
                f"[Cloudonix] created outbound voice trunk '{trunk_name}' "
                f"on domain {domain_id}"
            )
            return {**credentials, "outbound_trunk_uuid": created_uuid}
    except aiohttp.ClientError as e:
        logger.error(f"[Cloudonix] outbound trunk transport error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach Cloudonix to configure outbound trunk: {e}",
        ) from e


async def _preprocess_credentials_on_save(
    credentials: dict[str, Any],
) -> dict[str, Any]:
    credentials = await _fetch_domain_uuid(credentials)
    credentials = await _ensure_application_name(credentials)
    return await _ensure_outbound_trunk(credentials)


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
            name="outbound_trunk.enabled",
            label="Set up an outbound SIP trunk",
            type="boolean",
            description=(
                "Create and manage a public-outbound Cloudonix voice trunk. "
                "Turning this off deactivates a trunk previously created by Dograh."
            ),
            section="Outbound SIP trunk",
        ),
        ProviderUIField(
            name="outbound_trunk.name",
            label="Trunk Name",
            type="text",
            placeholder="e.g. dograh-carrier",
            description="A unique name for this trunk in your Cloudonix domain.",
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound SIP trunk",
        ),
        ProviderUIField(
            name="outbound_trunk.ip",
            label="Remote SIP Address",
            type="text",
            placeholder="sip.example.com",
            description=(
                "The carrier, PBX, or SIP service IP address or FQDN. "
                "Cloudonix does not perform SRV lookups."
            ),
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound SIP trunk",
        ),
        ProviderUIField(
            name="outbound_trunk.port",
            label="Remote SIP Port",
            type="number",
            required=False,
            placeholder="5060",
            description="Defaults to 5060.",
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound SIP trunk",
        ),
        ProviderUIField(
            name="outbound_trunk.transport",
            label="Transport",
            type="select",
            required=False,
            options=[
                ProviderUIOption(value="udp", label="UDP (default)"),
                ProviderUIOption(value="tcp", label="TCP"),
                ProviderUIOption(value="tls", label="TLS"),
            ],
            description="Transport used to connect to the remote SIP peer.",
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound SIP trunk",
        ),
        ProviderUIField(
            name="outbound_trunk.prefix",
            label="Technical Prefix",
            type="text",
            required=False,
            placeholder="e.g. +",
            description=(
                "Cloudonix prepends this value to every destination sent through "
                "the public-outbound trunk. Leave blank if your peer needs none."
            ),
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound SIP trunk",
        ),
        ProviderUIField(
            name="outbound_trunk.profile.authentication.username",
            label="Authentication Username",
            type="text",
            required=False,
            description=(
                "Optional SIP digest username. Username and password must be "
                "provided together."
            ),
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound trunk authentication",
        ),
        ProviderUIField(
            name="outbound_trunk.profile.authentication.password",
            label="Authentication Password",
            type="password",
            required=False,
            sensitive=True,
            description="Optional SIP digest password for the remote peer.",
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound trunk authentication",
        ),
        ProviderUIField(
            name="outbound_trunk.profile.authentication.overwrite_from",
            label="Use authentication username as caller ID",
            type="boolean",
            required=False,
            description=(
                "Enable only when your SIP provider requires the authentication "
                "username in the From header."
            ),
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound trunk authentication",
        ),
        ProviderUIField(
            name="outbound_trunk.profile.hostname",
            label="Cloudonix Border Gateway",
            type="text",
            required=False,
            description=(
                "Pin outbound calls to one Cloudonix Border Gateway hostname or "
                "IP. Normally leave blank to retain high availability."
            ),
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound trunk advanced settings",
        ),
        ProviderUIField(
            name="outbound_trunk.profile.domain",
            label="SIP To Domain",
            type="text",
            required=False,
            description="Override the domain in the SIP INVITE To header.",
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound trunk advanced settings",
        ),
        ProviderUIField(
            name="outbound_trunk.profile.ruri_domain",
            label="SIP Request-URI Domain",
            type="text",
            required=False,
            description=(
                "Override the Request-URI domain while still connecting to the "
                "remote SIP address above."
            ),
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound trunk advanced settings",
        ),
        ProviderUIField(
            name="outbound_trunk.profile.connection_timeout",
            label="Connection Timeout (seconds)",
            type="number",
            required=False,
            placeholder="10",
            description="Cloudonix defaults to 10 seconds.",
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound trunk advanced settings",
        ),
        ProviderUIField(
            name="outbound_trunk.profile.provisional_timeout",
            label="Provisional Timeout (seconds)",
            type="number",
            required=False,
            placeholder="2",
            description="Cloudonix defaults to 2 seconds.",
            visible_when=ProviderUICondition(
                field="outbound_trunk.enabled", equals=True
            ),
            section="Outbound trunk advanced settings",
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
        "outbound_trunk_uuid",
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
