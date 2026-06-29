"""Public lead-capture routes.

Thin handlers for the marketing/lead forms (Hire an Expert, Enterprise, and
post-signup Onboarding). They accept an unauthenticated submission — identity
is the email carried in the body — and delegate to the notifications service,
which emails the deployment owner (default: hardikagarwal@autosysai.dev).

Delivery is best-effort: when SMTP is unconfigured or the send fails the
service logs and returns without raising, so these endpoints always return a
success response and the user's form never breaks.
"""

from fastapi import APIRouter

from api.schemas.leads import LeadResponse, LeadSubmission
from api.services.notifications import send_lead_notification

router = APIRouter(prefix="/leads", tags=["leads"])


async def _handle(kind: str, submission: LeadSubmission) -> LeadResponse:
    payload = submission.model_dump(exclude_none=True)
    emailed = await send_lead_notification(kind, payload)
    return LeadResponse(status="ok", emailed=emailed)


@router.post("/hire-expert", response_model=LeadResponse)
async def submit_hire_expert(submission: LeadSubmission) -> LeadResponse:
    return await _handle("hire_expert", submission)


@router.post("/enterprise", response_model=LeadResponse)
async def submit_enterprise(submission: LeadSubmission) -> LeadResponse:
    return await _handle("enterprise", submission)


@router.post("/onboarding", response_model=LeadResponse)
async def submit_onboarding(submission: LeadSubmission) -> LeadResponse:
    return await _handle("onboarding", submission)
