"""GENERATED — do not edit by hand.

Regenerate with `python -m dograh_sdk.codegen` against the target
Dograh backend. Source of truth: the backend's model-backed node-spec
catalog served from `/api/v1/node-types`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional

from dograh_sdk.typed._base import TypedNode


@dataclass(kw_only=True)
class Paygent(TypedNode):
    """
    Cost Tracking and Billing  LLM hint: Paygent is a post-call usage-
    tracking and billing integration. It does not participate in the
    conversation graph and should not be connected to other nodes.
    """

    type: ClassVar[str] = 'paygent'

    paygent_api_key: str
    """
    API key used to authenticate requests to the Paygent REST API.
    """

    paygent_agent_id: str
    """
    The agent identifier registered in your Paygent account.
    """

    paygent_customer_id: str
    """
    Your Paygent customer / organisation ID.
    """

    name: str = 'Paygent'
    """
    Short identifier for this Paygent configuration.
    """

    paygent_enabled: bool = True
    """
    When false, Dograh skips all Paygent tracking for this call.
    """

    paygent_indicator: str = 'per-minute-call'
    """
    The indicator event name sent at the end of the call (e.g. per-minute-
    call).
    """

