"""Category-specific schemas derived from the authoritative tool request models."""

from functools import lru_cache
from typing import get_args

from pydantic import create_model

from api.schemas.tool import CreateToolRequest, ToolDefinition, UpdateToolRequest


@lru_cache(maxsize=36)
def tool_request_schema(
    category: str, operation: str = "create", destination_source: str = "static"
) -> dict:
    definitions = {
        get_args(model.model_fields["type"].annotation)[0]: model
        for model in get_args(get_args(ToolDefinition)[0])
    }
    if category not in definitions:
        return {"error": "Unknown category", "categories": sorted(definitions)}
    definition = definitions[category]
    base = CreateToolRequest if operation == "create" else UpdateToolRequest
    model = create_model(
        f"{operation.title()}{category.title().replace('_', '')}Tool",
        __base__=base,
        definition=(definition, ...)
        if operation == "create"
        else (definition | None, None),
    )
    schema = model.model_json_schema()
    if category == "transfer_call":
        config = schema["$defs"]["TransferCallConfig"]
        config["properties"]["destination_source"] = {
            "type": "string",
            "const": destination_source,
        }
        for field, mode in {
            "resolver": "dynamic",
            "context_mapping": "context_mapping",
        }.items():
            if destination_source != mode:
                config["properties"].pop(field)
        # The API exposes every transfer mode. This request needs only one;
        # retain definitions reachable through that mode's fields.
        definitions = schema.pop("$defs")
        reachable = {}

        def visit(value):
            if isinstance(value, dict):
                ref = value.get("$ref", "")
                if ref.startswith("#/$defs/"):
                    name = ref.removeprefix("#/$defs/")
                    if name not in reachable:
                        reachable[name] = definitions[name]
                        visit(definitions[name])
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(schema)
        schema["$defs"] = reachable
    return {
        "category": category,
        "operation": operation,
        "schema": _compact_schema(schema),
    }


def _compact_schema(value):
    """Drop display metadata while preserving field guidance and validation rules."""
    if isinstance(value, list):
        return [_compact_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: (
            {name: _compact_schema(schema) for name, schema in item.items()}
            if key in {"properties", "$defs"}
            else _compact_schema(item)
            if key in {"items", "anyOf", "oneOf", "allOf", "additionalProperties"}
            else item
        )
        for key, item in value.items()
        if key not in {"title", "llm_hint"}
        and not (key == "description" and value.get("type") == "object")
    }
