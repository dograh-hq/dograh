"""Request/response schemas for the public lead routes.

Lead payloads vary by form (Hire an Expert, Enterprise, Onboarding) and evolve
on the frontend, so the request model is intentionally permissive: known
contact fields are typed for clarity, but any extra keys the form sends are
accepted and forwarded to the notification email.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class LeadSubmission(BaseModel):
    """A lead/onboarding form submission. Extra fields are allowed and kept."""

    model_config = ConfigDict(extra="allow")

    # Common contact fields (all optional — shape differs per form).
    name: Optional[str] = None
    email: Optional[str] = None
    workEmail: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    source: Optional[str] = None
    origin: Optional[str] = None
    country: Optional[str] = None


class LeadResponse(BaseModel):
    """Always reports success — delivery is best-effort (see service layer)."""

    status: str = "ok"
    # True only when an email was actually handed to the SMTP server.
    emailed: bool = False
