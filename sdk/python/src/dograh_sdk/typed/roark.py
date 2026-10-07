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
class Roark(TypedNode):
    """
    Send the completed call to Roark for transcription, scoring and
    analytics

    LLM hint: Roark is a post-call analytics export. It does not participate
    in the conversation graph and should not be connected to other nodes.
    The call recording must be reachable from the public internet for Roark
    to ingest it.
    """

    type: ClassVar[str] = 'roark'

    roark_api_key: str
    """
    Project API key used to post completed calls to Roark.
    """

    name: str = 'Roark'
    """
    Short identifier for this Roark export configuration.
    """

    roark_enabled: bool = True
    """
    When false, Dograh skips exporting this call to Roark.
    """

    roark_agent_name: Optional[str] = None
    """
    Name of the agent in Roark. An agent with this exact name in the project
    is reused; otherwise Roark creates one.
    """

    roark_agent_id: Optional[str] = None
    """
    Optional. UUID of an existing Roark agent. Takes precedence over the
    agent name when both are set.
    """

    roark_send_transcript: bool = True
    """
    Send the transcript and tool calls Dograh captured during the call. Turn
    this off to have Roark transcribe the recording itself, for example to
    measure your own speech-to-text against Roark's. Roark then has no
    record of the tool calls either.
    """

    roark_send_gathered_context: bool = False
    """
    Also send the variables the agent gathered during the call as Roark call
    properties, so you can filter on them. Off by default because gathered
    context often holds personal data.
    """

