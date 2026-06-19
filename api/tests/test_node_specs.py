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

from api.services.workflow.dto import (
    ReactFlowDTO,
    all_node_type_names,
    get_node_data_model,
)
from api.services.workflow.node_data import BaseNodeData
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
    valid_names = all_node_type_names()
    assert spec.name in valid_names, (
        f"NodeSpec {spec.name!r} doesn't match any registered node type. "
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
    """Every registered node type must have a registered NodeSpec."""
    spec_names = {s.name for s in all_specs()}
    type_values = all_node_type_names()
    missing = type_values - spec_names
    assert not missing, f"Registered node types without specs: {sorted(missing)}"


def test_all_registered_node_models_inherit_base_node_data():
    for type_name in sorted(all_node_type_names()):
        data_model = get_node_data_model(type_name)
        assert data_model is not None, f"{type_name}: missing node data model"
        assert issubclass(data_model, BaseNodeData), (
            f"{type_name}: node data model must inherit BaseNodeData"
        )


@pytest.mark.parametrize(
    ("spec_name", "expected_order"),
    [
        (
            "startCall",
            [
                "name",
                "greeting_type",
                "greeting",
                "greeting_recording_id",
                "prompt",
                "allow_interrupt",
                "add_global_prompt",
                "delayed_start",
                "delayed_start_duration",
                "extraction_enabled",
                "extraction_prompt",
                "extraction_variables",
                "tool_uuids",
                "document_uuids",
                "pre_call_fetch_enabled",
                "pre_call_fetch_url",
                "pre_call_fetch_credential_uuid",
            ],
        ),
        (
            "agentNode",
            [
                "name",
                "prompt",
                "allow_interrupt",
                "add_global_prompt",
                "extraction_enabled",
                "extraction_prompt",
                "extraction_variables",
                "tool_uuids",
                "document_uuids",
            ],
        ),
        (
            "endCall",
            [
                "name",
                "prompt",
                "add_global_prompt",
                "extraction_enabled",
                "extraction_prompt",
                "extraction_variables",
            ],
        ),
        ("globalNode", ["name", "prompt"]),
        ("trigger", ["name", "enabled", "trigger_path"]),
        (
            "webhook",
            [
                "name",
                "enabled",
                "http_method",
                "endpoint_url",
                "credential_uuid",
                "custom_headers",
                "payload_template",
            ],
        ),
        (
            "qa",
            [
                "name",
                "qa_enabled",
                "qa_system_prompt",
                "qa_min_call_duration",
                "qa_voicemail_calls",
                "qa_sample_rate",
                "qa_use_workflow_llm",
                "qa_provider",
                "qa_model",
                "qa_api_key",
                "qa_endpoint",
            ],
        ),
        (
            "tuner",
            [
                "name",
                "tuner_enabled",
                "tuner_agent_id",
                "tuner_workspace_id",
                "tuner_api_key",
            ],
        ),
    ],
)
def test_node_spec_property_order_stable(spec_name: str, expected_order: list[str]):
    spec = next(spec for spec in all_specs() if spec.name == spec_name)
    assert [prop.name for prop in spec.properties] == expected_order


# ─────────────────────────────────────────────────────────────────────────
# `to_mcp_dict` projection — the lean view served by the `get_node_type`
# MCP tool. UI-only metadata is dropped so it doesn't poison LLM context;
# the full spec stays available to the frontend and SDK via other paths.
# ─────────────────────────────────────────────────────────────────────────

# Keys that are UI-rendering concerns and must never reach the LLM view, at
# either the node or property level.
_UI_ONLY_KEYS = frozenset(
    {
        "display_name",
        "icon",
        "category",
        "version",
        "placeholder",
        "display_options",
        "editor",
        "extra",
        "label",  # PropertyOption display string
    }
)


def _walk_dicts(node):
    """Yield every dict nested anywhere inside a projected structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_to_mcp_dict_drops_ui_only_keys(spec: NodeSpec):
    projected = spec.to_mcp_dict()
    for d in _walk_dicts(projected):
        leaked = _UI_ONLY_KEYS & d.keys()
        assert not leaked, f"{spec.name}: UI-only keys leaked into LLM view: {leaked}"


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_to_mcp_dict_omits_null_and_empty(spec: NodeSpec):
    """The lean view never emits null values — absent means unset/optional,
    which is what halves the noise versus the full model dump."""
    for d in _walk_dicts(spec.to_mcp_dict()):
        for key, value in d.items():
            assert value is not None, f"{spec.name}: {key!r} emitted as null"


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.name)
def test_to_mcp_dict_keeps_property_essentials(spec: NodeSpec):
    """Every property in the LLM view carries the minimum an LLM needs to
    author a value: machine name, type, and a description."""

    def _check(props: list[dict]):
        for prop in props:
            assert prop.get("name"), f"{spec.name}: property missing name"
            assert prop.get("type"), f"{spec.name}.{prop.get('name')}: missing type"
            assert prop.get("description"), (
                f"{spec.name}.{prop.get('name')}: missing description"
            )
            if prop.get("properties"):
                _check(prop["properties"])

    _check(spec.to_mcp_dict()["properties"])


def test_to_mcp_dict_retains_authoring_signal_startcall():
    """startCall is the richest core node — lock in that the projection
    keeps the fields an LLM actually authors against while shedding the rest."""
    spec = next(s for s in all_specs() if s.name == "startCall")
    projected = spec.to_mcp_dict()

    assert set(projected) == {
        "name",
        "description",
        "llm_hint",
        "properties",
        "examples",
        "graph_constraints",
    }

    props = {p["name"]: p for p in projected["properties"]}

    # Required field keeps `required`; optional fields omit it.
    assert props["prompt"]["required"] is True
    assert "required" not in props["greeting"]

    # Enum options project to bare values, dropping the UI label.
    assert props["greeting_type"]["options"] == [{"value": "text"}, {"value": "audio"}]

    # Validation bounds survive (they constrain valid authored values).
    assert props["delayed_start_duration"]["min_value"] == 0.1
    assert props["delayed_start_duration"]["max_value"] == 10.0

    # llm_hint survives where present (catalog-tool references).
    assert "list_recordings" in props["greeting_recording_id"]["llm_hint"]

    # fixed_collection rows recurse through the same projection.
    var_rows = {p["name"]: p for p in props["extraction_variables"]["properties"]}
    assert var_rows["type"]["options"] == [
        {"value": "string"},
        {"value": "number"},
        {"value": "boolean"},
    ]

    # graph_constraints drops its null sub-fields.
    assert projected["graph_constraints"] == {
        "min_incoming": 0,
        "max_incoming": 0,
        "min_instances": 1,
        "max_instances": 1,
    }
