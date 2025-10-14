from fastapi import APIRouter
from loguru import logger

from api.routes.campaign import router as campaign_router
from api.routes.integration import router as integration_router
from api.routes.looptalk import router as looptalk_router
from api.routes.organization import router as organization_router
from api.routes.organization_usage import router as organization_usage_router
from api.routes.reports import router as reports_router
from api.routes.rtc_offer import router as rtc_offer_router
from api.routes.s3_signed_url import router as s3_router
from api.routes.service_keys import router as service_keys_router
from api.routes.superuser import router as superuser_router
from api.routes.telephony import router as telephony_router
from api.routes.twilio import router as twilio_router  # TODO: Remove after migrating workflow_run_cost.py
from api.routes.user import router as user_router
from api.routes.webrtc_signaling import router as webrtc_signaling_router
from api.routes.workflow import router as workflow_router

router = APIRouter(
    tags=["main"],
    responses={404: {"description": "Not found"}},
)

router.include_router(telephony_router)  # New generic telephony routes
router.include_router(twilio_router)  # TODO: Remove after migrating workflow_run_cost.py
router.include_router(rtc_offer_router)
router.include_router(superuser_router)
router.include_router(workflow_router)
router.include_router(user_router)
router.include_router(campaign_router)
router.include_router(integration_router)
router.include_router(organization_router)
router.include_router(s3_router)
router.include_router(service_keys_router)
router.include_router(looptalk_router)
router.include_router(organization_usage_router)
router.include_router(reports_router)
router.include_router(webrtc_signaling_router)


@router.get("/health")
async def health():
    logger.debug("Health endpoint called")
    return {"message": "OK"}
