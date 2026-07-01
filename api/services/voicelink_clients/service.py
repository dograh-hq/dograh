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
import secrets as _stdlib_secrets
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from api.db import db_client
from api.services.auth.admin_emails import is_admin_email
from api.services.voicelink_clients.client import (
    VoiceLinkClientError,
    VoiceLinkClientsClient,
    get_voicelink_clients_client,
)
from api.services.voicelink_clients.secrets import (
    decrypt_provision_secret,
    encrypt_provision_secret,
)

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


async def _find_voicelink_client_id_by_username(
    client: VoiceLinkClientsClient, username: Optional[str]
) -> Optional[str]:
    """Recover a client's VoiceLink id by matching username in the reseller list.

    The create response occasionally omits the id, and orgs provisioned before
    id-capture carry ``client_id=None``. Matching ``GET /v1/reseller/clients`` by
    username recovers it (``id`` in each record IS the client id). Best-effort —
    returns None on any failure or no match (so callers fall back to creating).
    """
    if not username:
        return None
    target = username.strip().lower()
    try:
        for record in await client.list_clients():
            if str(record.get("username") or "").strip().lower() == target:
                cid = record.get("id") or record.get("client_id")
                return str(cid) if cid is not None else None
    except Exception:
        logger.warning(
            "VoiceLink client_id backfill lookup failed", exc_info=True
        )
    return None


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
    if not client_id:
        # The create envelope didn't carry the id — recover it from the list so
        # KYC can scope to this client (KYC needs the client_id).
        client_id = await _find_voicelink_client_id_by_username(client, username)
    await db_client.update_organization_voicelink(
        organization_id,
        client_id=client_id,
        username=username,
        status=VOICELINK_STATUS_PROVISIONED,
        error=None,
        # Retain the encrypted client password: we authenticate as this client
        # (username + password -> access token) when dialing on its VoiceLink
        # account, and surface it in the admin Clients view.
        provision_secret=encrypt_provision_secret(password),
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


def generate_client_password(length: int = 24) -> str:
    """A strong random password for a platform-managed VoiceLink client.

    The end user never logs into VoiceLink directly (the reseller manages the
    client), so this is used only for ``create_client`` and never surfaced.
    """
    return _stdlib_secrets.token_urlsafe(length)


def resolve_org_owner(organization: Any) -> Optional[Any]:
    """The org's owner user: local signup names orgs ``org_<user.provider_id>``;
    falls back to the earliest member. Returns None when the org has no members.
    """
    users = list(getattr(organization, "users", None) or [])
    if not users:
        return None
    for user in users:
        if f"org_{user.provider_id}" == organization.provider_id:
            return user
    return min(users, key=lambda u: u.id)


async def ensure_voicelink_client(
    organization_id: int,
    *,
    client: Optional[VoiceLinkClientsClient] = None,
) -> Dict[str, Any]:
    """Idempotently ensure the org has a provisioned VoiceLink client.

    No-op when the org already carries a ``voicelink_client_id``. Best-effort —
    never raises — so callers (KYC entry, buy-number, the retry sweep) can
    fire-and-forget. Uses the org's stored signup secret when present, else a
    generated password. Skips the deployment owner (ADMIN_EMAILS), who uses the
    reseller's own account, and no-ops when reseller credentials are unset.

    Returns the same ``{"status", "client_id", "username", "error"}`` shape as
    :func:`provision_voicelink_client`.
    """
    try:
        organization = await db_client.get_organization_with_users(organization_id)
        if organization is None:
            return {
                "status": None,
                "client_id": None,
                "username": None,
                "error": "organization_not_found",
            }

        if organization.voicelink_client_id:
            return {
                "status": VOICELINK_STATUS_PROVISIONED,
                "client_id": organization.voicelink_client_id,
                "username": organization.voicelink_username,
                "error": None,
            }

        client = client or get_voicelink_clients_client()
        if not client.is_configured:
            return {
                "status": None,
                "client_id": None,
                "username": organization.voicelink_username,
                "error": "reseller_credentials_unset",
            }

        # The client may already exist on VoiceLink but its id was lost locally.
        # Recover it before creating a duplicate; also self-heals orgs marked
        # "provisioned" with a NULL client_id (KYC then scopes correctly).
        existing_id = await _find_voicelink_client_id_by_username(
            client, organization.voicelink_username
        )
        if existing_id:
            await db_client.update_organization_voicelink(
                organization_id,
                client_id=existing_id,
                status=VOICELINK_STATUS_PROVISIONED,
                error=None,
            )
            return {
                "status": VOICELINK_STATUS_PROVISIONED,
                "client_id": existing_id,
                "username": organization.voicelink_username,
                "error": None,
            }

        owner = resolve_org_owner(organization)
        if owner is None or not getattr(owner, "email", None):
            return {
                "status": VOICELINK_STATUS_PENDING,
                "client_id": None,
                "username": organization.voicelink_username,
                "error": "organization_has_no_owner_email",
            }

        if is_admin_email(owner.email):
            return {
                "status": None,
                "client_id": None,
                "username": organization.voicelink_username,
                "error": "admin_owner_uses_reseller_account",
            }

        password = decrypt_provision_secret(
            organization.voicelink_provision_secret
        ) or generate_client_password()

        return await provision_voicelink_client(
            organization_id,
            email=owner.email,
            password=password,
            name=getattr(owner, "name", None),
            username=organization.voicelink_username or None,
            client=client,
        )
    except Exception:
        logger.warning(
            f"ensure_voicelink_client failed unexpectedly for org {organization_id}",
            exc_info=True,
        )
        return {
            "status": VOICELINK_STATUS_PENDING,
            "client_id": None,
            "username": None,
            "error": "unexpected_error",
        }
