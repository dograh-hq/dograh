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
class GlobalNode(TypedNode):
    """
    Persona/tone appended to every agent node's prompt.  LLM hint: System-
    level prompt appended to every prompted node whose `add_global_prompt`
    is true. Use it for persona, tone, and shared rules that apply across
    the entire conversation. At most one global node per workflow.
    """

    type: ClassVar[str] = 'globalNode'

    name: str = 'Global Node'
    """
    Short identifier shown in the canvas and call logs. Has no runtime
    effect.
    """

    prompt: str = "You are a helpful assistant whose mode of interaction with the user is voice. So don't use any special characters which can not be pronounced. Use short sentences and simple language."
    """
    Text appended to every prompted node's system prompt when that node has
    `add_global_prompt=true`. Supports {{template_variables}}.
    """

