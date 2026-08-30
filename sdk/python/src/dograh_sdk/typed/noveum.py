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
class Noveum(TypedNode):
    """
    Export the completed call to Noveum for tracing and evaluation  LLM
    hint: Noveum is a post-call observability export. It does not
    participate in the conversation graph and should not be connected to
    other nodes.
    """

    type: ClassVar[str] = 'noveum'

    noveum_api_key: str
    """
    Bearer token used when exporting the completed call to Noveum.
    """

    noveum_project: str
    """
    The Noveum project the call trace is exported into.
    """

    name: str = 'Noveum'
    """
    Short identifier for this Noveum export configuration.
    """

    noveum_enabled: bool = True
    """
    When false, Dograh skips exporting this call to Noveum.
    """

    noveum_environment: str = 'production'
    """
    Environment label stamped on exported traces (e.g. production, staging).
    """

    noveum_record_audio: bool = True
    """
    Capture per-segment STT/TTS audio and the full-conversation recording
    for audio evaluation on Noveum.
    """

