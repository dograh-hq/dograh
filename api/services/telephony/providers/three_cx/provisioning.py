"""Provision a 3CX PJSIP trunk on the bridging Asterisk via ARA Postgres.

Called by ``ProviderSpec.preprocess_credentials_on_save`` whenever a
TelephonyConfiguration of type ``three_cx`` is created or updated. Writes
the standard six-table PJSIP realtime set:

* ``ps_auths`` — userpass auth for outbound REGISTER + inbound 401 challenge
* ``ps_aors`` — single contact, qualify keepalive
* ``ps_endpoints`` — codec list, dialplan context, auth/aor references
* ``ps_registrations`` — outbound REGISTER toward the 3CX cloud SBC
* ``extensions`` (x N) — dialplan rows produced by ``dialplan.build_dialplan_rows``

Idempotent on re-save: every row keyed by the deterministic
``endpoint_id_for(sip_domain, extension)`` is deleted first and then
re-inserted in the same transaction. The preprocessor is allowed to do
I/O (registry.py docstring) but must remain re-entrant from the route
layer's point of view.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from fastapi import HTTPException
from loguru import logger

from .ara_db import AraNotConfiguredError, get_pool
from .dialplan import build_dialplan_rows

# Stasis app name as configured in the bridging Asterisk's
# websocket_client.conf. Mirrors the ``app_name`` field on the
# configuration — see runbook §1.
_STASIS_APP_KEY = "app_name"

# Default codecs: G.711a + G.711μ cover 3CX defaults; "ulaw,alaw" is the
# ordered allow list, "all" the disallow base.
_DEFAULT_ALLOW = "ulaw,alaw"
_DEFAULT_DISALLOW = "all"

# Asterisk-side transport name configured by the admin in pjsip.conf
# (e.g. ``transport-udp``). The runbook tells the admin how to set this
# up; the provider just references it by name. Override per-deployment
# via env var on the calling process if necessary.
_TRANSPORT_NAME_DEFAULT = "transport-udp"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def endpoint_id_for(sip_domain: str, extension: str) -> str:
    """Deterministic, globally-unique Asterisk endpoint id for this trunk.

    Form: ``dograh_<slug(sip_domain)>_<extension>``. Two TelephonyConfigurations
    can't legitimately collide because two Asterisks can't simultaneously
    register the same (domain, extension) pair upstream anyway.

    >>> endpoint_id_for('1156.3cx.cloud', '12611')
    'dograh_1156_3cx_cloud_12611'
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (sip_domain or "").lower()).strip("_")
    ext = re.sub(r"[^A-Za-z0-9]+", "", extension or "")
    if not slug or not ext:
        raise ValueError(
            f"Cannot derive endpoint_id from sip_domain={sip_domain!r} "
            f"extension={extension!r}"
        )
    return f"dograh_{slug}_{ext}"


async def _provision_3cx_trunk(credentials: Dict[str, Any]) -> Dict[str, Any]:
    """Preprocessor hook — writes the ARA rows for this trunk.

    Returns the credentials dict unchanged (the provider re-derives
    ``endpoint_id`` deterministically at runtime, so nothing extra needs
    to be persisted).

    Raises ``HTTPException`` on validation failure or ARA write failure so
    the route layer aborts the DB save — matches the Cloudonix pattern.
    """
    required = ("sip_domain", "extension", "sip_password", "app_name")
    missing = [k for k in required if not credentials.get(k)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"3CX provision: missing required credential(s): {missing}",
        )

    sip_domain = credentials["sip_domain"].strip().lower()
    extension = credentials["extension"].strip()
    sip_password = credentials["sip_password"]
    stasis_app = credentials[_STASIS_APP_KEY]
    strip_prefix = credentials.get("strip_prefix", "")

    endpoint_id = endpoint_id_for(sip_domain, extension)
    transport_name = credentials.get("transport_name", _TRANSPORT_NAME_DEFAULT)

    try:
        pool = await get_pool()
    except AraNotConfiguredError as e:
        raise HTTPException(status_code=400, detail=str(e))

    dialplan_rows = build_dialplan_rows(
        endpoint_id=endpoint_id,
        extension=extension,
        stasis_app=stasis_app,
        strip_prefix=strip_prefix,
    )

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _delete_existing(conn, endpoint_id)
                await _insert_auth(conn, endpoint_id, extension, sip_password)
                await _insert_aor(conn, endpoint_id)
                await _insert_endpoint(
                    conn, endpoint_id, transport_name, sip_domain
                )
                await _insert_registration(
                    conn,
                    endpoint_id=endpoint_id,
                    transport_name=transport_name,
                    sip_domain=sip_domain,
                    extension=extension,
                )
                await _insert_extensions(conn, dialplan_rows)
    except Exception as e:
        logger.exception(f"[3CX/ARA] provisioning failed for {endpoint_id}: {e}")
        raise HTTPException(
            status_code=502,
            detail=(
                f"3CX provisioning failed while writing to Asterisk ARA: {e}. "
                f"No TelephonyConfiguration was saved."
            ),
        )

    logger.info(
        f"[3CX/ARA] provisioned endpoint={endpoint_id} "
        f"(dialplan rows: {len(dialplan_rows)})"
    )
    return credentials


async def _deprovision_3cx_trunk(credentials: Dict[str, Any]) -> None:
    """Remove all ARA rows for a given trunk.

    Not wired into a hook today — the registry only exposes the
    save-time hook. Exposed as a callable so a future
    ``post_delete`` extension or admin tooling can use it.
    """
    sip_domain = (credentials.get("sip_domain") or "").strip().lower()
    extension = (credentials.get("extension") or "").strip()
    if not sip_domain or not extension:
        return
    endpoint_id = endpoint_id_for(sip_domain, extension)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _delete_existing(conn, endpoint_id)
    logger.info(f"[3CX/ARA] deprovisioned endpoint={endpoint_id}")


# ---------------------------------------------------------------------------
# Per-table writers
# ---------------------------------------------------------------------------


async def _delete_existing(conn, endpoint_id: str) -> None:
    """Strip every ARA row owned by this endpoint_id. Idempotent."""
    await conn.execute("DELETE FROM ps_registrations WHERE id = $1", endpoint_id)
    await conn.execute("DELETE FROM ps_endpoints   WHERE id = $1", endpoint_id)
    await conn.execute("DELETE FROM ps_aors        WHERE id = $1", endpoint_id)
    await conn.execute("DELETE FROM ps_auths       WHERE id = $1", endpoint_id)
    # Dialplan rows live under two derived contexts.
    await conn.execute(
        "DELETE FROM extensions WHERE context IN ($1, $2)",
        f"{endpoint_id}-inbound",
        f"{endpoint_id}-outbound",
    )


async def _insert_auth(conn, endpoint_id: str, username: str, password: str) -> None:
    await conn.execute(
        """
        INSERT INTO ps_auths (id, auth_type, username, password)
        VALUES ($1, 'userpass', $2, $3)
        """,
        endpoint_id,
        username,
        password,
    )


async def _insert_aor(conn, endpoint_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO ps_aors (id, max_contacts, qualify_frequency)
        VALUES ($1, 1, 60)
        """,
        endpoint_id,
    )


async def _insert_endpoint(
    conn, endpoint_id: str, transport_name: str, sip_domain: str
) -> None:
    await conn.execute(
        """
        INSERT INTO ps_endpoints (
            id, transport, aors, auth, context,
            disallow, allow, from_domain, identify_by
        ) VALUES ($1, $2, $1, $1, $3, $4, $5, $6, 'auth_username,username')
        """,
        endpoint_id,
        transport_name,
        f"{endpoint_id}-inbound",
        _DEFAULT_DISALLOW,
        _DEFAULT_ALLOW,
        sip_domain,
    )


async def _insert_registration(
    conn,
    *,
    endpoint_id: str,
    transport_name: str,
    sip_domain: str,
    extension: str,
) -> None:
    server_uri = f"sip:{sip_domain}"
    client_uri = f"sip:{extension}@{sip_domain}"
    await conn.execute(
        """
        INSERT INTO ps_registrations (
            id, transport, outbound_auth, server_uri, client_uri,
            contact_user, expiration, retry_interval
        ) VALUES ($1, $2, $1, $3, $4, $5, 300, 60)
        """,
        endpoint_id,
        transport_name,
        server_uri,
        client_uri,
        extension,
    )


async def _insert_extensions(conn, rows: list[dict]) -> None:
    for r in rows:
        await conn.execute(
            """
            INSERT INTO extensions (context, exten, priority, app, appdata)
            VALUES ($1, $2, $3, $4, $5)
            """,
            r["context"],
            r["exten"],
            r["priority"],
            r["app"],
            r["appdata"],
        )
