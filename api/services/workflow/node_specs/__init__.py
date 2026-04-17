"""Node specification registry.

Adding a new node type:
1. Create a new module under this package, define a `SPEC: NodeSpec`.
2. Add it to the imports + REGISTRY below.
3. The Pydantic discriminated-union variant in dto.py must use the same
   `name` value as `SPEC.name`.
"""

from __future__ import annotations

from api.services.workflow.node_specs._base import (
    SPEC_VERSION,
    DisplayOptions,
    GraphConstraints,
    MigrationSpec,
    NodeCategory,
    NodeExample,
    NodeSpec,
    PropertyOption,
    PropertySpec,
    PropertyType,
    get_migration,
    register_migration,
)

REGISTRY: dict[str, NodeSpec] = {}


def register(spec: NodeSpec) -> NodeSpec:
    """Register a NodeSpec in the global registry. Returns the spec for
    chaining at module top-level: `SPEC = register(NodeSpec(...))`."""
    if spec.name in REGISTRY:
        raise ValueError(
            f"Duplicate NodeSpec registration for {spec.name!r}. "
            f"Each node type must have exactly one spec."
        )
    REGISTRY[spec.name] = spec
    return spec


def get_spec(name: str) -> NodeSpec | None:
    return REGISTRY.get(name)


def all_specs() -> list[NodeSpec]:
    """All registered specs, sorted by name for stable output."""
    return [REGISTRY[name] for name in sorted(REGISTRY)]


__all__ = [
    "SPEC_VERSION",
    "REGISTRY",
    "DisplayOptions",
    "GraphConstraints",
    "MigrationSpec",
    "NodeCategory",
    "NodeExample",
    "NodeSpec",
    "PropertyOption",
    "PropertySpec",
    "PropertyType",
    "all_specs",
    "get_migration",
    "get_spec",
    "register",
    "register_migration",
]


# Side-effect imports — each module's `register(SPEC)` call populates REGISTRY.
# Keep at module bottom so the registry helpers are defined first.
from api.services.workflow.node_specs import (  # noqa: E402, F401
    agent,
    end_call,
    global_node,
    qa,
    start_call,
    trigger,
    webhook,
)

# Wire up registrations from the SPEC constants in each module.
for _module in (start_call, agent, end_call, global_node, trigger, webhook, qa):
    register(_module.SPEC)
del _module
