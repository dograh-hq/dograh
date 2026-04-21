"""GENERATED — do not edit by hand.

Regenerate with `python -m dograh_sdk.codegen` against the target
Dograh backend. Source of truth: each node's NodeSpec in the backend's
`api/services/workflow/node_specs/` directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional

from dograh_sdk.typed._base import TypedNode


@dataclass(kw_only=True)
class Trigger(TypedNode):
    """
    Public HTTP endpoint that launches the workflow.  LLM hint: Exposes a
    public HTTP POST endpoint. External systems call the URL (derived from
    the auto-generated `trigger_path`) to launch this workflow. Requires an
    API key in the `X-API-Key` header.
    """

    type: ClassVar[str] = 'trigger'

    name: str = 'API Trigger'
    """
    Short identifier shown in the canvas. No runtime effect.
    """

    enabled: bool = True
    """
    When false, the trigger URL returns 404.
    """

    trigger_path: Optional[str] = None
    """
    Auto-generated UUID-style path segment that uniquely identifies this
    trigger. Do not edit manually.
    """

