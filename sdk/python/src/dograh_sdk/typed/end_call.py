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
class EndCall_Extraction_variablesRow:
    """
    Each entry declares one variable to capture from the conversation, with
    its name, data type, and a per-variable extraction hint.
    """

    name: str
    """
    snake_case identifier used downstream.
    """
    type: Literal['string', 'number', 'boolean'] = 'string'
    """
    The data type of the extracted value.
    """
    prompt: Optional[str] = None
    """
    Per-variable hint describing what to look for in the conversation.
    """

@dataclass(kw_only=True)
class EndCall(TypedNode):
    """
    Closes the conversation and hangs up.  LLM hint: Terminal node that
    politely closes the conversation. Variable extraction can run before
    hangup. A workflow can have multiple endCall nodes reached via different
    edge conditions.
    """

    type: ClassVar[str] = 'endCall'

    prompt: str
    """
    Agent system prompt for the closing exchange. Supports
    {{template_variables}} from extraction or pre-call fetch.
    """

    name: str = 'End Call'
    """
    Short identifier shown in call logs. Should describe the ending context
    (e.g., 'Successful close', 'Polite decline').
    """

    add_global_prompt: bool = False
    """
    When true and a Global node exists, prepends the global prompt to this
    node's prompt at runtime.
    """

    extraction_enabled: bool = False
    """
    When true, runs an LLM extraction pass before hangup to capture
    variables from the conversation.
    """

    extraction_prompt: Optional[str] = None
    """
    Overall instructions guiding how variables should be extracted from the
    conversation.
    """

    extraction_variables: list[EndCall_Extraction_variablesRow] = field(default_factory=list)
    """
    Each entry declares one variable to capture from the conversation, with
    its name, data type, and a per-variable extraction hint.
    """

