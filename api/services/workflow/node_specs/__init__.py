"""Node specification registry.

Core node specs live in this package. Third-party integration node specs live
under `api.services.integrations/<name>/` and register through the integration
registry so they don't need edits here.
"""

from __future__ import annotations

from api.services.workflow.node_specs._base import (
    SPEC_VERSION,
    DisplayOptions,
    GraphConstraints,
    NodeCategory,
    NodeExample,
    NodeSpec,
    PropertyOption,
    PropertySpec,
    PropertyType,
    evaluate_display_options,
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
    if name in REGISTRY:
        return REGISTRY[name]

    from api.services.integrations import get_node_spec

    return get_node_spec(name)


def all_specs() -> list[NodeSpec]:
    """All registered specs, sorted by name for stable output."""
    from api.services.integrations import all_node_specs

    specs = {spec.name: spec for spec in REGISTRY.values()}
    specs.update({spec.name: spec for spec in all_node_specs()})
    return [specs[name] for name in sorted(specs)]


__all__ = [
    "SPEC_VERSION",
    "REGISTRY",
    "DisplayOptions",
    "GraphConstraints",
    "NodeCategory",
    "NodeExample",
    "NodeSpec",
    "PropertyOption",
    "PropertySpec",
    "PropertyType",
    "all_specs",
    "evaluate_display_options",
    "get_spec",
    "register",
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
for _module in (
    start_call,
    agent,
    end_call,
    global_node,
    trigger,
    webhook,
    qa,
):
    register(_module.SPEC)
del _module
