"""VoiceLink client provisioning.

``provision_voicelink_client`` builds the ``/v1/reseller/client/create``
payload (channel counts / rates come from env defaults), calls VoiceLink,
and stores the outcome on the organization:

- success → ``voicelink_status = "provisioned"`` + ``client_id``/``username``
- failure (e.g. 422 — no channels available) → ``voicelink_status = "pending"``
  with the upstream error message so the admin Clients view can retry.

``provision_voicelink_client_for_signup`` is the best-effort signup hook: it
skips ADMIN_EMAILS users (the deployment owner) and unset reseller creds,
and never raises — signup must not fail because of VoiceLink.

The plaintext password is forwarded to VoiceLink only and is NEVER logged
or stored.
"""

import os
import re
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from api.db import db_client
from api.services.auth.admin_emails import is_admin_email
from api.services.voicelink_clients.client import (
    VoiceLinkClientError,
    VoiceLinkClientsClient,
    get_voicelink_clients_client,
)
from api.services.voicelink_clients.secrets import encrypt_provision_secret

VOICELINK_STATUS_PROVISIONED = "provisioned"
VOICELINK_STATUS_PENDING = "pending"


def derive_username(email: str, organization_id: int) -> str:
    """Derive a unique VoiceLink username from the email local-part.

    The org id suffix keeps usernames unique across our reseller account
    even when two signups share an email local-part.
    """
    local_part = email.split("@", 1)[0]
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "", local_part).strip("._-") or "client"
    return f"{sanitized}.{organization_id}"


def split_signup_name(
    name: Optional[str], organization_id: int
) -> Tuple[str, str]:
    """Split a signup display name into (first_name, last_name).

    Falls back to "Client" / an org-derived last name when the name is
    missing or has a single token.
    """
    tokens = (name or "").split()
    first_name = tokens[0] if tokens else "Client"
    last_name = " ".join(tokens[1:]) or f"Org{organization_id}"
    return first_name, last_name


def _env_int(var: str, default: int) -> int:
    try:
        return int(os.getenv(var) or default)
    except (TypeError, ValueError):
        return default


def _env_float(var: str, default: float) -> float:
    try:
        return float(os.getenv(var) or default)
    except (TypeError, ValueError):
        return default


def build_create_client_payload(
    *,
    first_name: str,
    last_name: str,
    username: str,
    email: str,
    password: str,
) -> Dict[str, Any]:
    """Payload for ``POST /v1/reseller/client/create`` with env-driven defaults."""
    return {
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "email": email,
        "password": password,
        "channel_count": _env_int("VOICELINK_DEFAULT_CHANNELS", 1),
        "negative_threshold": 0,
        "pulse_seconds": 60,
        "inbound_rate": _env_float("VOICELINK_DEFAULT_INBOUND_RATE", 1),
        "outbound_rate": _env_float("VOICELINK_DEFAULT_OUTBOUND_RATE", 1),
    }


def _extract_client_id(envelope: Dict[str, Any]) -> Optional[str]:
    """Best-effort client id extraction — VoiceLink response bodies are thin."""
    data = envelope.get("data") or {}
    if not isinstance(data, dict):
        return None
    candidate = data.get("client_id") or data.get("id")
    if candidate is None and isinstance(data.get("client"), dict):
        candidate = data["client"].get("client_id") or data["client"].get("id")
    return str(candidate) if candidate is not None else None


async def provision_voicelink_client(
    organization_id: int,
    *,
    email: str,
    password: str,
    name: Optional[str] = None,
    username: Optional[str] = None,
    client: Optional[VoiceLinkClientsClient] = None,
) -> Dict[str, Any]:
    """Create a VoiceLink client for the organization and persist the outcome.

    Returns ``{"status", "client_id", "username", "error"}``. On failure the
    org is marked ``pending`` with the upstream error; an existing
    ``voicelink_client_id`` is left untouched so a partially-provisioned org
    is never wiped by a failed retry.
    """
    client = client or get_voicelink_clients_client()
    username = username or derive_username(email, organization_id)
    first_name, last_name = split_signup_name(name, organization_id)

    payload = build_create_client_payload(
        first_name=first_name,
        last_name=last_name,
        username=username,
        email=email,
        password=password,
    )

    try:
        envelope = await client.create_client(payload)
    except VoiceLinkClientError as e:
        error = str(e)
        await db_client.update_organization_voicelink(
            organization_id,
            username=username,
            status=VOICELINK_STATUS_PENDING,
            error=error,
            # Keep an encrypted copy of the password so admin "Create client"
            # can reuse the same platform password later.
            provision_secret=encrypt_provision_secret(password),
        )
        logger.warning(
            f"VoiceLink provisioning pending for org {organization_id}: {error}"
        )
        return {
            "status": VOICELINK_STATUS_PENDING,
            "client_id": None,
            "username": username,
            "error": error,
        }

    client_id = _extract_client_id(envelope)
    await db_client.update_organization_voicelink(
        organization_id,
        client_id=client_id,
        username=username,
        status=VOICELINK_STATUS_PROVISIONED,
        error=None,
        # Org is provisioned — the stored secret is no longer needed.
        provision_secret=None,
    )
    logger.info(
        f"VoiceLink client provisioned for org {organization_id} "
        f"(client_id={client_id}, username={username})"
    )
    return {
        "status": VOICELINK_STATUS_PROVISIONED,
        "client_id": client_id,
        "username": username,
        "error": None,
    }


async def provision_voicelink_client_for_signup(
    *,
    organization_id: int,
    email: str,
    password: str,
    name: Optional[str] = None,
) -> None:
    """Best-effort signup hook — NEVER raises and never fails signup.

    Skips entirely for ADMIN_EMAILS users (the deployment owner does not get
    a VoiceLink client) and when the reseller credentials are unset.
    """
    try:
        if is_admin_email(email):
            logger.info(
                f"Skipping VoiceLink provisioning for admin email signup "
                f"(org {organization_id})"
            )
            return

        client = get_voicelink_clients_client()
        if not client.is_configured:
            logger.info(
                "Skipping VoiceLink provisioning — reseller credentials unset"
            )
            # Stash the encrypted password so admin "Create client" can
            # provision later (once reseller creds are set) with the same
            # platform password. No-op when no provisioning key is configured.
            secret = encrypt_provision_secret(password)
            if secret:
                await db_client.update_organization_voicelink(
                    organization_id, provision_secret=secret
                )
            return

        await provision_voicelink_client(
            organization_id,
            email=email,
            password=password,
            name=name,
            client=client,
        )
    except Exception:
        # Catch-all: provisioning errors are recorded as "pending" inside
        # provision_voicelink_client; anything unexpected lands here.
        logger.warning(
            f"VoiceLink provisioning failed unexpectedly for org "
            f"{organization_id}",
            exc_info=True,
        )
        try:
            await db_client.update_organization_voicelink(
                organization_id,
                status=VOICELINK_STATUS_PENDING,
                error="Unexpected provisioning error — see API logs",
            )
        except Exception:
            logger.warning(
                "Failed to record pending VoiceLink status", exc_info=True
            )
