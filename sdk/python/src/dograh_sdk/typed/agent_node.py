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
class AgentNode_Extraction_variablesRow:
    """
    Each entry declares one variable to capture from the conversation, with
    its name, type, and per-variable hint.
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
class AgentNode(TypedNode):
    """
    Conversational step — the LLM runs one focused exchange.  LLM hint: Mid-
    call step executed by the LLM. Most workflows are a chain of agent nodes
    connected by edges that describe transition conditions. Each agent node
    can invoke tools and reference documents.
    """

    type: ClassVar[str] = 'agentNode'

    prompt: str
    """
    Agent system prompt for this step. Supports {{template_variables}} from
    extraction or pre-call fetch.
    """

    name: str = 'Agent'
    """
    Short identifier for this step (e.g., 'Qualify Budget'). Appears in call
    logs and edge transition tools.
    """

    allow_interrupt: bool = True
    """
    When true, the user can interrupt the agent mid-utterance. Set false for
    non-interruptible disclosures.
    """

    add_global_prompt: bool = True
    """
    When true and a Global node exists, prepends the global prompt to this
    node's prompt at runtime.
    """

    extraction_enabled: bool = False
    """
    When true, runs an LLM extraction pass on transition out of this node to
    capture variables from the conversation.
    """

    extraction_prompt: Optional[str] = None
    """
    Overall instructions guiding variable extraction.
    """

    extraction_variables: list[AgentNode_Extraction_variablesRow] = field(default_factory=list)
    """
    Each entry declares one variable to capture from the conversation, with
    its name, type, and per-variable hint.
    """

    tool_uuids: list[str] = field(default_factory=list)
    """
    Tools the agent can invoke during this step.
    """

    document_uuids: list[str] = field(default_factory=list)
    """
    Documents the agent can reference during this step.
    """

