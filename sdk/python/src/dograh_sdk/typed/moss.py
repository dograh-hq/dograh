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
class Moss(TypedNode):
    """
    Let the agent search a Moss index during the call  LLM hint: Moss is a
    knowledge retrieval configuration node. It does not participate in the
    conversation graph and should not be connected to other nodes. When
    enabled, Start Call and Agent nodes search the configured Moss index: in
    ambient mode on every caller turn before the LLM answers, in tool mode
    through a search_moss_index tool the LLM calls.
    """

    type: ClassVar[str] = 'moss'

    name: str = 'Moss'
    """
    Short identifier for this Moss index configuration.
    """

    moss_enabled: bool = True
    """
    When false, agents do not search the Moss index.
    """

    moss_mode: Literal['ambient', 'tool'] = 'ambient'
    """
    When the agent searches the index.
    """

    moss_index_name: Optional[str] = None
    """
    Name of the Moss index the agent searches.
    """

    moss_project_id: Optional[str] = None
    """
    Moss project that owns the index.
    """

    moss_project_key: Optional[str] = None
    """
    Moss project key used to download the index.
    """

    moss_index_description: Optional[str] = None
    """
    What the index contains. With the search tool, which tool mode and
    speech to speech calls use, the agent reads this to decide when to
    search.
    """

    moss_top_k: float = 3
    """
    How many documents each search returns to the agent.
    """

    moss_alpha: float = 0.8
    """
    Blend of semantic and keyword matching: 1.0 is semantic only, 0.0 is
    keyword only.
    """

