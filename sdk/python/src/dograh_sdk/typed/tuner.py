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
class Tuner(TypedNode):
    """
    Export the completed call to Tuner for Agent Observability  LLM hint:
    Tuner is a post-call observability export. It does not participate in
    the conversation graph and should not be connected to other nodes.
    """

    type: ClassVar[str] = 'tuner'

    tuner_agent_id: str
    """
    The agent identifier registered in your Tuner workspace.
    """

    tuner_workspace_id: float
    """
    Your numeric Tuner workspace ID.
    """

    tuner_api_key: str
    """
    Bearer token used when posting completed calls to Tuner.
    """

    name: str = 'Tuner'
    """
    Short identifier for this Tuner export configuration.
    """

    tuner_enabled: bool = True
    """
    When false, Dograh skips exporting this call to Tuner.
    """

