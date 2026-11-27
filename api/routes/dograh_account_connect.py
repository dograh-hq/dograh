"""Connect a self-hosted install to a Dograh account.

Self-hosted installs call ``/start`` and ``/poll``. The hosted app calls the
``/requests`` routes for the signed-in person, whose verified email and
selected team decide which account the install's key draws on. ``/status``
answers, in either deployment mode, whether the organization has a usable
Dograh key at all.
"""

from fastapi import APIRouter, Depends, HTTPException

from api.constants import DEPLOYMENT_MODE
from api.db.models import UserModel
from api.schemas.dograh_account_connect import (
    AccountConnectDecision,
    AccountConnectPoll,
    AccountConnectPollRequest,
    AccountConnectRequest,
    AccountConnectStart,
    AccountConnectStartRequest,
    DograhAccountStatus,
)
from api.services.auth.depends import (
    VerifiedStackIdentity,
    get_user_with_selected_organization,
    get_verified_stack_identity,
    get_verified_stack_user,
)
from api.services.dograh_account_connect import (
    get_dograh_account_status,
    poll_account_connect,
    start_account_connect,
)
from api.services.mps_service_key_client import mps_service_key_client

router = APIRouter(prefix="/dograh-account/connect", tags=["dograh-account"])


def _require_self_hosted() -> None:
    if DEPLOYMENT_MODE != "oss":
        raise HTTPException(
            status_code=404,
            detail="Only self-hosted installations connect to a Dograh account",
        )


@router.get(
    "/status",
    response_model=DograhAccountStatus,
    operation_id="get_dograh_account_status",
)
async def get_status(
    user: UserModel = Depends(get_user_with_selected_organization),
) -> DograhAccountStatus:
    return await get_dograh_account_status(user.selected_organization_id)


@router.post(
    "/start",
    response_model=AccountConnectStart,
    operation_id="start_dograh_account_connect",
)
async def start(
    request: AccountConnectStartRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
) -> AccountConnectStart:
    _require_self_hosted()
    return await start_account_connect(
        install_url=request.install_url, created_by=str(user.provider_id)
    )


@router.post(
    "/poll",
    response_model=AccountConnectPoll,
    operation_id="poll_dograh_account_connect",
)
async def poll(
    request: AccountConnectPollRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
) -> AccountConnectPoll:
    _require_self_hosted()
    return await poll_account_connect(
        user.selected_organization_id, request.device_code
    )


@router.get(
    "/requests/{user_code}",
    response_model=AccountConnectRequest,
    operation_id="get_dograh_account_connect_request",
)
async def get_request(
    user_code: str, user: UserModel = Depends(get_verified_stack_user)
) -> AccountConnectRequest:
    found = await mps_service_key_client.get_account_connect_request(
        user_code,
        organization_id=user.selected_organization_id,
        created_by=str(user.provider_id),
    )
    return AccountConnectRequest.model_validate(found)


@router.post(
    "/requests/{user_code}/approve",
    response_model=AccountConnectDecision,
    operation_id="approve_dograh_account_connect_request",
)
async def approve(
    user_code: str,
    verified: VerifiedStackIdentity = Depends(get_verified_stack_identity),
) -> AccountConnectDecision:
    # The install shows the team name once it is connected.
    await mps_service_key_client.decide_account_connect_request(
        user_code,
        approve=True,
        organization_id=verified.user.selected_organization_id,
        created_by=str(verified.user.provider_id),
        team_name=verified.team_name,
    )
    return AccountConnectDecision(status="approved")


@router.post(
    "/requests/{user_code}/deny",
    response_model=AccountConnectDecision,
    operation_id="deny_dograh_account_connect_request",
)
async def deny(
    user_code: str, user: UserModel = Depends(get_verified_stack_user)
) -> AccountConnectDecision:
    await mps_service_key_client.decide_account_connect_request(
        user_code,
        approve=False,
        organization_id=user.selected_organization_id,
        created_by=str(user.provider_id),
    )
    return AccountConnectDecision(status="denied")
