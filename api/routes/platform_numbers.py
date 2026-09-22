from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user

router = APIRouter(prefix="/platform/numbers", tags=["platform-numbers"])


class ClaimNumberRequest(BaseModel):
    set_as_default: bool = True


@router.get("", response_model=List[Dict[str, Any]])
async def list_platform_numbers(
    user: UserModel = Depends(get_user),
):
    """List available platform inventory numbers for the organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    numbers = await db_client.list_platform_numbers(user.selected_organization_id)
    return numbers


@router.post("/{number_id}/claim")
async def claim_platform_number(
    number_id: int,
    request: ClaimNumberRequest = ClaimNumberRequest(),
    user: UserModel = Depends(get_user),
):
    """Claim or purchase a platform number for the organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    try:
        from api.services.telephony_billing_service import telephony_billing_service
        result = await telephony_billing_service.claim_platform_number_with_plan_check(
            phone_number_id=number_id,
            organization_id=user.selected_organization_id,
            set_as_default=request.set_as_default,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to claim number: {str(e)}")


@router.post("/{number_id}/release")
async def release_platform_number(
    number_id: int,
    user: UserModel = Depends(get_user),
):
    """Release a claimed platform number back to platform inventory."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    try:
        from api.services.telephony_billing_service import telephony_billing_service
        await telephony_billing_service.release_platform_number(
            phone_number_id=number_id,
            organization_id=user.selected_organization_id,
        )
        return {"success": True, "message": "Phone number released back to platform inventory"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to release number: {str(e)}")
