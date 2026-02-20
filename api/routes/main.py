from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from api.routes.auth import router as auth_router
from api.routes.campaign import router as campaign_router
from api.routes.credentials import router as credentials_router
from api.routes.integration import router as integration_router
from api.routes.knowledge_base import router as knowledge_base_router
from api.routes.looptalk import router as looptalk_router
from api.routes.organization import router as organization_router
from api.routes.organization_usage import router as organization_usage_router
from api.routes.public_agent import router as public_agent_router
from api.routes.public_download import router as public_download_router
from api.routes.public_embed import router as public_embed_router
from api.routes.reports import router as reports_router
from api.routes.s3_signed_url import router as s3_router
from api.routes.service_keys import router as service_keys_router
from api.routes.superuser import router as superuser_router
from api.routes.telephony import router as telephony_router
from api.routes.tool import router as tool_router
from api.routes.turn_credentials import router as turn_credentials_router
from api.routes.user import router as user_router
from api.routes.webrtc_signaling import router as webrtc_signaling_router
from api.routes.workflow import router as workflow_router
from api.routes.workflow_embed import router as workflow_embed_router

router = APIRouter(
    tags=["main"],
    responses={404: {"description": "Not found"}},
)

router.include_router(telephony_router)
router.include_router(superuser_router)
router.include_router(workflow_router)
router.include_router(user_router)
router.include_router(campaign_router)
router.include_router(credentials_router)
router.include_router(tool_router)
router.include_router(integration_router)
router.include_router(organization_router)
router.include_router(s3_router)
router.include_router(service_keys_router)
router.include_router(looptalk_router)
router.include_router(organization_usage_router)
router.include_router(reports_router)
router.include_router(webrtc_signaling_router)
router.include_router(turn_credentials_router)
router.include_router(public_embed_router)
router.include_router(public_agent_router)
router.include_router(public_download_router)
router.include_router(workflow_embed_router)
router.include_router(knowledge_base_router)
router.include_router(auth_router)


class HealthResponse(BaseModel):
    status: str
    version: str
    backend_api_endpoint: str
    deployment_mode: str
    auth_provider: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    from api.constants import APP_VERSION, AUTH_PROVIDER, DEPLOYMENT_MODE
    from api.utils.common import get_backend_endpoints

    logger.debug("Health endpoint called")
    backend_endpoint, _ = await get_backend_endpoints()
    return HealthResponse(
        status="ok",
        version=APP_VERSION,
        backend_api_endpoint=backend_endpoint,
        deployment_mode=DEPLOYMENT_MODE,
        auth_provider=AUTH_PROVIDER,
    )
