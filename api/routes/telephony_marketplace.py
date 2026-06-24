"""Telephony marketplace routes — buy a phone number after KYC, charged to credits.

GET /numbers (available pool) · GET /my-numbers (org's assigned) · POST /buy
(KYC gate -> resolve the org's VoiceLink client -> charge credits -> map the DID).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.constants import NUMBER_SETUP_MINUTES
from api.db import db_client
from api.db.models import UserModel
from api.services import telephony_marketplace as mkt
from api.services.auth.depends import get_user
from api.services.voicelink_clients.client import VoiceLinkClientError
from api.services.voicelink_kyc.gating import assert_org_kyc_complete

router = APIRouter(prefix="/telephony/marketplace", tags=["telephony"])


class BuyNumberRequest(BaseModel):
    did_id: int


def _org(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="no_organization_selected")
    return user.selected_organization_id


@router.get("/numbers")
async def available_numbers(user: UserModel = Depends(get_user)):
    _org(user)
    return {
        "numbers": await mkt.list_available_numbers(),
        "setup_seconds": NUMBER_SETUP_MINUTES * 60,
    }


@router.get("/my-numbers")
async def my_numbers(user: UserModel = Depends(get_user)):
    org = _org(user)
    o = await db_client.get_organization_by_id(org)
    return {"numbers": await mkt.list_org_numbers(o.voicelink_client_id if o else None)}


@router.post("/buy")
async def buy_number(body: BuyNumberRequest, user: UserModel = Depends(get_user)):
    org = _org(user)
    # Compliance + provisioning gates.
    await assert_org_kyc_complete(org)
    o = await db_client.get_organization_by_id(org)
    client_id = o.voicelink_client_id if o else None
    if not client_id:
        raise HTTPException(status_code=400, detail="telephony_account_not_provisioned")

    # Never trust the client-supplied did_id: it MUST be in the reseller's
    # available pool (prevents grabbing an arbitrary / another org's DID).
    available = await mkt.list_available_numbers()
    if not any(
        n.get("did_id") is not None and int(n["did_id"]) == body.did_id
        for n in available
    ):
        raise HTTPException(status_code=409, detail="number_unavailable")

    # Charge FIRST, atomically + conditionally (race-safe), so concurrent buys
    # can't double-spend. Unlimited (NULL) orgs and zero-cost are never charged.
    cost = NUMBER_SETUP_MINUTES * 60
    charged = False
    if cost > 0 and (await db_client.get_free_call_seconds_remaining(org)) is not None:
        if not await db_client.try_charge_call_seconds(org, cost):
            raise HTTPException(status_code=402, detail="insufficient_credits")
        charged = True

    # Assign the DID; refund the charge if the external map fails.
    try:
        await mkt.assign_number(client_id, body.did_id)
    except VoiceLinkClientError as e:
        if charged:
            await db_client.add_call_seconds(org, cost)
        raise HTTPException(status_code=502, detail=f"assign_failed: {e}")

    new_balance = await db_client.get_free_call_seconds_remaining(org)
    return {"ok": True, "did_id": body.did_id, "balance_seconds": new_balance}
