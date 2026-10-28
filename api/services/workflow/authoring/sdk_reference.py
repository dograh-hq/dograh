"""Small SDK field reference derived from the existing core node specifications."""

import json

from api.services.workflow.node_specs import PropertySpec, PropertyType, get_spec

CORE_NODE_TYPES = ("globalNode", "startCall", "agentNode", "endCall")
_FIELDS = frozenset(
    {
        "name",
        "prompt",
        "greeting",
        "extraction_enabled",
        "extraction_prompt",
        "extraction_variables",
    }
)


def _field_signature(prop: PropertySpec) -> str:
    if prop.options:
        kind = " | ".join(json.dumps(option.value) for option in prop.options)
    elif prop.type == PropertyType.fixed_collection:
        kind = (
            "{ "
            + "; ".join(_field_signature(p) for p in prop.properties or [])
            + " }[]"
        )
    elif prop.type == PropertyType.mention_textarea:
        kind = "string"
    else:
        kind = prop.type.value
    return f"{prop.name}{'' if prop.required else '?'}: {kind}"


def core_sdk_reference() -> str:
    lines = [
        "## Core SDK factories",
        "",
        "Signatures describe object-literal inputs; ? means optional. Omit unused fields.",
        "Exactly one startCall; at most one globalNode, shared by prompted nodes and",
        "never connected by edges. endCall ends the call and has no outgoing edges.",
        "Use greeting for text spoken before the opening prompt. Prompts are system",
        "instructions and support {{variables}} from known caller context or extraction.",
        "Set extraction_enabled=true to capture extraction_variables; extraction_prompt",
        "guides the extraction pass and each variable's prompt describes its value.",
        "Other settings keep their defaults. Names identify steps in source and logs.",
        "",
        "```text",
    ]
    for name in CORE_NODE_TYPES:
        spec = get_spec(name)
        if spec is None:
            raise ValueError(f"Missing core node specification: {name}")
        fields = "; ".join(
            _field_signature(prop) for prop in spec.properties if prop.name in _FIELDS
        )
        lines.append(f"{name}({{ {fields} }})")
    return "\n".join([*lines, "```"])
