"""Spec schema for node definitions.

A `NodeSpec` is the single source of truth for a node type. It drives:
- Pydantic validation (the per-type DTOs in dto.py mirror these property types)
- The generic UI renderer (frontend reads specs via /api/v1/node-types)
- The LLM SDK (constructors and JSON-Schema derived from these specs)

Every property's `description` is LLM-readable copy — treat it as production
documentation, not internal notes. Spec lint enforces non-empty descriptions
and example coverage.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

# Spec contract version. Bump when adding new PropertyType values or making
# breaking changes to the NodeSpec wire shape. SDK clients warn on mismatch.
SPEC_VERSION = "1.0.0"


class PropertyType(str, Enum):
    """Bounded vocabulary of property types the renderer dispatches on.

    Adding a value here requires a matching arm in the frontend
    `<PropertyInput>` switch and (where relevant) the SDK codegen template.
    """

    string = "string"
    number = "number"
    boolean = "boolean"
    options = "options"  # single-select dropdown
    multi_options = "multi_options"  # multi-select
    fixed_collection = "fixed_collection"  # repeating rows of sub-properties
    json = "json"  # arbitrary JSON object editor

    # Domain-specific reference types — values are UUIDs/keys looked up against
    # a reference catalog (list_tools, list_documents, list_recordings,
    # list_credentials).
    tool_refs = "tool_refs"
    document_refs = "document_refs"
    recording_ref = "recording_ref"
    credential_ref = "credential_ref"

    # Domain-specific input widgets
    mention_textarea = "mention_textarea"  # textarea with {{var}} mentions
    url = "url"


class NodeCategory(str, Enum):
    """Drives grouping in the AddNodePanel UI."""

    call_node = "call_node"
    global_node = "global_node"
    trigger = "trigger"
    integration = "integration"


class DisplayOptions(BaseModel):
    """Conditional visibility rules.

    `show` keys are AND-combined: this property is visible only when EVERY
    referenced field's value matches one of the listed values.

    `hide` keys are OR-combined: this property is hidden when ANY referenced
    field's value matches one of the listed values.

    Example:
        DisplayOptions(show={"extraction_enabled": [True]})
        DisplayOptions(show={"greeting_type": ["audio"]})
    """

    show: Optional[dict[str, list[Any]]] = None
    hide: Optional[dict[str, list[Any]]] = None

    model_config = ConfigDict(extra="forbid")


class PropertyOption(BaseModel):
    """An option in an `options` or `multi_options` dropdown."""

    value: str | int | bool | float
    label: str
    description: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class PropertySpec(BaseModel):
    """Single field on a node.

    Every PropertySpec must carry a non-empty `description` written for an LLM
    that has never seen the platform — the spec lint enforces this.
    """

    name: str
    type: PropertyType
    display_name: str
    description: str = Field(
        ...,
        min_length=1,
        description="LLM-readable explanation of what this field controls.",
    )
    default: Any = None
    required: bool = False
    placeholder: Optional[str] = None

    display_options: Optional[DisplayOptions] = None

    # For `options` / `multi_options`
    options: Optional[list[PropertyOption]] = None

    # For `fixed_collection` — sub-properties of each row
    properties: Optional[list["PropertySpec"]] = None

    # Validation hints. Enforced by Pydantic where possible.
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None

    # Renderer hint, e.g. "textarea" vs single-line for `string`.
    editor: Optional[str] = None

    # Free-form metadata for renderer-specific behavior. Use sparingly.
    extra: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


PropertySpec.model_rebuild()


class NodeExample(BaseModel):
    """A worked example LLMs can pattern-match. Keep small and realistic."""

    name: str
    description: Optional[str] = None
    data: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class GraphConstraints(BaseModel):
    """Per-node-type graph rules. WorkflowGraph enforces these at validation."""

    min_incoming: Optional[int] = None
    max_incoming: Optional[int] = None
    min_outgoing: Optional[int] = None
    max_outgoing: Optional[int] = None

    model_config = ConfigDict(extra="forbid")


class MigrationSpec(BaseModel):
    """Declared migration step (JSON-serializable view).

    The migrate callable is registered out-of-band via `register_migration()`
    and never serialized — LLM and frontend consumers only see version
    metadata and warn on mismatch.
    """

    from_version: str
    to_version: str
    description: str

    model_config = ConfigDict(extra="forbid")


class NodeSpec(BaseModel):
    """Single source of truth for a node type."""

    name: str  # machine name; matches the Pydantic discriminator value
    display_name: str
    description: str = Field(
        ...,
        min_length=1,
        description="LLM-readable explanation of what this node does.",
    )
    category: NodeCategory
    icon: str  # lucide-react icon name (e.g., "Play")
    version: str = "1.0.0"
    properties: list[PropertySpec]
    examples: list[NodeExample] = Field(default_factory=list)
    migrations: list[MigrationSpec] = Field(default_factory=list)
    graph_constraints: Optional[GraphConstraints] = None

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Migration callables registry
#
# Migration logic is server-side Python (not JSON), so it lives in a separate
# registry keyed by (node_name, from_version, to_version).
# ---------------------------------------------------------------------------

MigrationCallable = Callable[[dict[str, Any]], dict[str, Any]]
_MIGRATION_REGISTRY: dict[tuple[str, str, str], MigrationCallable] = {}


def register_migration(
    node_name: str,
    from_version: str,
    to_version: str,
    migrate: MigrationCallable,
) -> None:
    """Register the migrate callable for a declared MigrationSpec.

    The corresponding MigrationSpec must also appear in the NodeSpec's
    `migrations` list — this registry only holds the callable.
    """
    _MIGRATION_REGISTRY[(node_name, from_version, to_version)] = migrate


def get_migration(
    node_name: str, from_version: str, to_version: str
) -> Optional[MigrationCallable]:
    return _MIGRATION_REGISTRY.get((node_name, from_version, to_version))
