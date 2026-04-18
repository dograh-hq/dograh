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
class StartCall_Extraction_variablesRow:
    """
    Each entry declares one variable to capture, with its name, data type,
    and per-variable extraction hint.
    """

    name: str
    """
    snake_case identifier used downstream.
    """
    type: Literal['string', 'number', 'boolean'] = 'string'
    """
    Data type of the extracted value.
    """
    prompt: Optional[str] = None
    """
    Per-variable hint describing what to look for.
    """

@dataclass(kw_only=True)
class StartCall(TypedNode):
    """
    Entry point of the workflow — plays a greeting and opens the
    conversation.  LLM hint: The entry point of every workflow (exactly one
    required). Plays an optional greeting, can fetch context from an
    external API before the call begins, and executes the first
    conversational turn.
    """

    type: ClassVar[str] = 'startCall'

    prompt: str
    """
    Agent system prompt for the opening turn. Supports
    {{template_variables}} from pre-call fetch and the initial context.
    """

    name: str = 'Start Call'
    """
    Short identifier shown in the canvas and call logs.
    """

    greeting_type: Literal['text', 'audio'] = 'text'
    """
    Whether the optional greeting is spoken via TTS from text or played from
    a pre-recorded audio file.
    """

    greeting: Optional[str] = None
    """
    Text spoken via TTS at the start of the call. Supports
    {{template_variables}}. Leave empty to skip the greeting.
    """

    greeting_recording_id: Optional[str] = None
    """
    Pre-recorded audio file played at the start of the call.
    """

    allow_interrupt: bool = False
    """
    When true, the user can interrupt the agent mid-utterance.
    """

    add_global_prompt: bool = True
    """
    When true and a Global node exists, prepends the global prompt to this
    node's prompt at runtime.
    """

    delayed_start: bool = False
    """
    When true, the agent waits before speaking after pickup. Useful for
    outbound calls where the called party needs a moment to settle.
    """

    delayed_start_duration: float = 2.0
    """
    Seconds to wait before the agent speaks. 0.1–10.
    """

    extraction_enabled: bool = False
    """
    When true, runs an LLM extraction pass on transition out of this node to
    capture variables from the opening turn.
    """

    extraction_prompt: Optional[str] = None
    """
    Overall instructions guiding variable extraction.
    """

    extraction_variables: list[StartCall_Extraction_variablesRow] = field(default_factory=list)
    """
    Each entry declares one variable to capture, with its name, data type,
    and per-variable extraction hint.
    """

    tool_uuids: list[str] = field(default_factory=list)
    """
    Tools the agent can invoke during the opening turn.
    """

    document_uuids: list[str] = field(default_factory=list)
    """
    Documents the agent can reference.
    """

    pre_call_fetch_enabled: bool = False
    """
    When true, makes a POST request to an external API before the call
    starts and merges the JSON response into the call context as template
    variables.
    """

    pre_call_fetch_url: Optional[str] = None
    """
    URL the pre-call POST request is sent to. The request body includes
    caller and called numbers.
    """

    pre_call_fetch_credential_uuid: Optional[str] = None
    """
    Optional credential attached to the pre-call request.
    """

