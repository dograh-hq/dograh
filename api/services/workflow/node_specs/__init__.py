"""Node specification registry.

Core node specs are generated from the workflow DTO models. Third-party
integration node specs live under `api.services.integrations/<name>/` and
register through the integration registry so they don't need edits here.
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
from api.services.workflow.node_specs.model_spec import build_spec

REGISTRY: dict[str, NodeSpec] = {}
_CORE_SPECS_LOADED = False


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
    _ensure_core_registered()
    if name in REGISTRY:
        return REGISTRY[name]

    from api.services.integrations import get_node_spec

    return get_node_spec(name)


def all_specs() -> list[NodeSpec]:
    """All registered specs, sorted by name for stable output."""
    _ensure_core_registered()
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


def _ensure_core_registered() -> None:
    global _CORE_SPECS_LOADED
    if _CORE_SPECS_LOADED:
        return

    from api.services.workflow.dto import _CORE_NODE_DATA_CLASSES

    for model_cls in _CORE_NODE_DATA_CLASSES.values():
        if model_cls.__node_spec_metadata__.name in REGISTRY:
            continue
        register(build_spec(model_cls))
    _CORE_SPECS_LOADED = True
