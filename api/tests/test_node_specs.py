"""Spec-quality lint.

Catches drift between NodeSpecs and the rest of the system before it lands:
- Placeholder/empty descriptions
- Missing examples
- display_options referencing fields that don't exist
- Examples that don't validate against the per-type Pydantic DTO
- Spec name not matching a discriminator value in dto.py
"""

from __future__ import annotations

import re

import pytest

from api.services.workflow.dto import NodeType, ReactFlowDTO
from api.services.workflow.node_specs import (
    NodeSpec,
    PropertySpec,
    PropertyType,
    all_specs,
)

PLACEHOLDER_DESCRIPTION_PATTERN = re.compile(
    r"^\s*(todo|fixme|tbd|xxx|\.\.\.|placeholder|description|n/?a|\?)\s*\.?\s*$",
    re.IGNORECASE,
)


def _walk_properties(props: list[PropertySpec], path: str = ""):
    """Yield (full_path, property) for every property and nested sub-property."""
    for prop in props:
        full_path = f"{path}.{prop.name}" if path else prop.name
        yield full_path, prop
        if prop.properties:
            yield from _walk_properties(prop.properties, full_path)


# ─────────────────────────────────────────────────────────────────────────
# Lint
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_node_spec_has_non_placeholder_description(spec: NodeSpec):
    assert spec.description.strip(), f"{spec.name}: empty description"
    assert not PLACEHOLDER_DESCRIPTION_PATTERN.match(spec.description), (
        f"{spec.name}: description looks like a placeholder: {spec.description!r}"
    )
    assert len(spec.description) >= 20, (
        f"{spec.name}: description too short to be useful for an LLM "
        f"({len(spec.description)} chars)"
    )


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_node_spec_has_at_least_one_example(spec: NodeSpec):
    assert spec.examples, (
        f"{spec.name}: must have at least one NodeExample so LLMs have a "
        f"realistic shape to pattern-match."
    )


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_property_descriptions_non_placeholder(spec: NodeSpec):
    for path, prop in _walk_properties(spec.properties):
        assert prop.description.strip(), f"{spec.name}.{path}: empty description"
        assert not PLACEHOLDER_DESCRIPTION_PATTERN.match(prop.description), (
            f"{spec.name}.{path}: description looks like a placeholder: "
            f"{prop.description!r}"
        )


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_display_options_reference_real_fields(spec: NodeSpec):
    """A property's display_options must only reference sibling property
    names. Nested properties are scoped to their parent's siblings."""

    def _check(scope_props: list[PropertySpec], scope_path: str = ""):
        names_in_scope = {p.name for p in scope_props}
        for prop in scope_props:
            current_path = f"{scope_path}.{prop.name}" if scope_path else prop.name
            if prop.display_options:
                refs = set((prop.display_options.show or {}).keys()) | set(
                    (prop.display_options.hide or {}).keys()
                )
                missing = refs - names_in_scope
                assert not missing, (
                    f"{spec.name}.{current_path}: display_options references "
                    f"unknown sibling fields: {sorted(missing)}"
                )
            if prop.properties:
                _check(prop.properties, current_path)

    _check(spec.properties)


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_options_properties_have_options(spec: NodeSpec):
    for path, prop in _walk_properties(spec.properties):
        if prop.type in (PropertyType.options, PropertyType.multi_options):
            assert prop.options, (
                f"{spec.name}.{path}: type={prop.type.value} requires at "
                f"least one PropertyOption."
            )


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_fixed_collection_has_sub_properties(spec: NodeSpec):
    for path, prop in _walk_properties(spec.properties):
        if prop.type == PropertyType.fixed_collection:
            assert prop.properties, (
                f"{spec.name}.{path}: fixed_collection requires nested "
                f"`properties` describing each row."
            )


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_spec_name_matches_dto_discriminator(spec: NodeSpec):
    valid_names = {t.value for t in NodeType}
    assert spec.name in valid_names, (
        f"NodeSpec {spec.name!r} doesn't match any NodeType discriminator. "
        f"Valid: {sorted(valid_names)}"
    )


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_examples_validate_against_dto(spec: NodeSpec):
    """Each NodeExample.data must pass per-type DTO validation. This stops
    examples from drifting away from the actual wire schema."""
    for ex in spec.examples:
        wire_node = {
            "id": "example",
            "type": spec.name,
            "position": {"x": 0, "y": 0},
            "data": ex.data,
        }
        # Build a minimal valid graph: example node plus a synthetic peer if
        # graph_constraints require an incoming or outgoing edge.
        nodes = [wire_node]
        edges: list[dict] = []
        constraints = spec.graph_constraints

        if constraints and (constraints.min_outgoing or 0) > 0:
            nodes.append(
                {
                    "id": "downstream",
                    "type": "endCall",
                    "position": {"x": 0, "y": 0},
                    "data": {"name": "End", "prompt": "End", "is_end": True},
                }
            )
            edges.append(
                {
                    "id": "e_out",
                    "source": "example",
                    "target": "downstream",
                    "data": {"label": "next", "condition": "next"},
                }
            )

        if constraints and (constraints.min_incoming or 0) > 0:
            nodes.append(
                {
                    "id": "upstream",
                    "type": "startCall",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "name": "Start",
                        "prompt": "Hello",
                        "is_start": True,
                    },
                }
            )
            edges.append(
                {
                    "id": "e_in",
                    "source": "upstream",
                    "target": "example",
                    "data": {"label": "in", "condition": "in"},
                }
            )

        # Validate. If this raises, the example is broken.
        ReactFlowDTO.model_validate({"nodes": nodes, "edges": edges})


def test_all_dto_types_have_specs():
    """Every NodeType discriminator value must have a registered NodeSpec —
    catches the case where someone adds a new node type to dto.py but
    forgets to author a spec."""
    spec_names = {s.name for s in all_specs()}
    type_values = {t.value for t in NodeType}
    missing = type_values - spec_names
    assert not missing, f"NodeType discriminators without specs: {sorted(missing)}"
