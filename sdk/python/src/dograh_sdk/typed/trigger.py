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
    Public HTTP endpoints that launch the workflow.  LLM hint: Exposes two
    public HTTP POST endpoints derived from the auto-generated
    `trigger_path`:   • Production:
    `<backend>/api/v1/public/agent/<trigger_path>` — runs the published
    agent. Use this from production systems.   • Test:
    `<backend>/api/v1/public/agent/test/<trigger_path>` — runs the latest
    draft, useful for verifying changes before publishing. Falls back to the
    published agent when no draft exists. Both require an API key in the
    `X-API-Key` header.
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
    trigger. Used in both URLs:   • Production:
    `/api/v1/public/agent/<trigger_path>` — executes the published agent.
    • Test: `/api/v1/public/agent/test/<trigger_path>` — executes the latest
    draft. Do not edit manually.
    """

