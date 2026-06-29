"""Request/response schemas for the Claude-powered agent builder.

The frontend posts a free-form business ``prompt`` plus an optional structured
``business`` questionnaire; the backend returns the id of the draft workflow it
generated so the UI can redirect to ``/workflow/<id>``.
"""

from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, Field


class AgentBuildBusiness(BaseModel):
    """Optional structured questionnaire that supplements the free-form prompt.

    Every field is optional — the free-form ``prompt`` is the only required
    input. Anything supplied here is fed to Claude as extra grounding and,
    for ``order_webhook_url``, wired directly into a webhook node + http_api
    tool by the generator.
    """

    business_type: Optional[str] = None
    sells_to: Optional[str] = None
    catalog: Optional[str] = None
    order_webhook_url: Optional[str] = None
    customer_lookup: Optional[str] = None
    kyc_required: Optional[bool] = None
    pricing_source: Optional[str] = None
    persona_name: Optional[str] = None
    language: Optional[str] = None
    voice: Optional[str] = None
    goal: Optional[str] = None
    objections: Optional[str] = None
    cross_sell: Optional[str] = None
    fulfillment: Optional[str] = None

    model_config = {"extra": "allow"}


class AgentBuildRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Free-form business description.")
    # Accept a typed questionnaire or a permissive dict — both serialize the same.
    business: Optional[Union[AgentBuildBusiness, dict]] = None


class AgentBuildResponse(BaseModel):
    workflow_id: Union[int, str]
    status: str = "draft"
    name: str
    warnings: list[str] = Field(default_factory=list)
    editor_path: str
