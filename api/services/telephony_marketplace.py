"""Telephony marketplace — browse the reseller's available numbers + assign one.

Thin wrappers over the VoiceLink reseller API: list available DIDs, assign a DID to
the org's VoiceLink client, and list the org's assigned DIDs. Org/credit/KYC
orchestration lives in routes/telephony_marketplace.py.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from api.services.voicelink_clients.client import (
    VoiceLinkClientError,
    get_voicelink_clients_client,
)


def _norm_did(d: dict) -> dict:
    return {
        "did_id": d.get("did_id") or d.get("id"),
        "did_number": d.get("did_number"),
        "type_label": d.get("type_label"),
        "country_code": d.get("country_code"),
        "user_status": d.get("user_status"),
        "user_status_label": d.get("user_status_label"),
    }


async def list_available_numbers() -> list[dict]:
    """Available (unassigned) DIDs in the reseller pool. Empty if none/unconfigured."""
    client = get_voicelink_clients_client()
    if not client.is_configured:
        return []
    try:
        dids = await client.available_dids()
    except VoiceLinkClientError as e:
        logger.warning(f"VoiceLink available-dids failed: {e}")
        return []
    # user_status 1 = Available.
    return [_norm_did(d) for d in dids if str(d.get("user_status", "1")) == "1"]


async def assign_number(client_id: str, did_id) -> None:
    """Map an available DID to the org's VoiceLink client. Raises VoiceLinkClientError."""
    client = get_voicelink_clients_client()
    await client.map_did(
        {
            "client_id": client_id,
            "did_id": did_id,
            "call_recording": 1,
            "user_status": 2,  # Assigned
            "client_auto_renew": 1,
        }
    )


async def list_org_numbers(client_id: Optional[str]) -> list[dict]:
    """DIDs currently assigned to the org's VoiceLink client."""
    client = get_voicelink_clients_client()
    if not client_id or not client.is_configured:
        return []
    try:
        clients = await client.list_clients()
    except VoiceLinkClientError:
        return []
    for c in clients:
        if str(c.get("id")) == str(client_id):
            return [_norm_did(d) for d in (c.get("dids") or [])]
    return []
