"""GoHighLevel CRM adapter (the universal voice-AI CRM integration).

Two-step write per the GHL API v2: upsert the contact (deduped by phone within the
Location) then attach a note. Auth is a static Private Integration Token (Bearer) +
the required `Version` header — both handled without OAuth. Docs:
https://highlevel.stoplight.io/docs/integrations (contacts/upsert, contacts/{id}/notes).
"""

from __future__ import annotations

import httpx
from loguru import logger

from api.services.integrations.crm.base import (
    CallLog,
    CRMProvider,
    CRMSyncResult,
    normalize_phone,
    render_call_note,
)

GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"


class GoHighLevelProvider(CRMProvider):
    name = "gohighlevel"

    def __init__(self, api_key: str, location_id: str, timeout: float = 15.0):
        self._api_key = api_key
        self._location_id = location_id
        self._timeout = timeout

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Version": GHL_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def sync_call(self, call: CallLog) -> CRMSyncResult:
        phone = normalize_phone(call.phone)
        if not phone:
            return CRMSyncResult(ok=False, detail="no_phone")

        contact_body: dict = {"locationId": self._location_id, "phone": phone}
        if call.name:
            contact_body["name"] = call.name
        if call.email:
            contact_body["email"] = call.email

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # 1) Upsert contact (deduped by phone within the Location).
                up = await client.post(
                    f"{GHL_BASE}/contacts/upsert",
                    headers=self._headers,
                    json=contact_body,
                )
                if not up.is_success:
                    detail = _err(up)
                    logger.warning(f"GHL contact upsert failed: {up.status_code} {detail}")
                    return CRMSyncResult(ok=False, detail=f"upsert_failed: {detail}")

                body = up.json() if up.content else {}
                contact_id = (body.get("contact") or {}).get("id") or body.get("id")
                if not contact_id:
                    return CRMSyncResult(ok=False, detail="no_contact_id_in_response")

                # 2) Log the call as a note on the contact.
                note = await client.post(
                    f"{GHL_BASE}/contacts/{contact_id}/notes",
                    headers=self._headers,
                    json={"body": render_call_note(call)},
                )
        except Exception as exc:  # network / timeout
            logger.warning(f"GHL sync failed (network): {exc}")
            return CRMSyncResult(ok=False, detail=f"network_error: {exc}")

        if not note.is_success:
            # Contact was upserted; note failed — partial success.
            logger.warning(f"GHL note failed for contact {contact_id}: {_err(note)}")
            return CRMSyncResult(
                ok=False, detail=f"note_failed: {_err(note)}", contact_id=contact_id
            )

        logger.info(f"GHL synced call for contact {contact_id} ({phone})")
        return CRMSyncResult(ok=True, detail="synced", contact_id=contact_id)


def _err(resp: httpx.Response) -> str:
    try:
        body = resp.json() if resp.content else {}
        return str(body.get("message") or body.get("error") or f"http_{resp.status_code}")
    except Exception:
        return f"http_{resp.status_code}"
