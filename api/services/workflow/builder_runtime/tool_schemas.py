"""Category-specific schemas derived from the authoritative tool request models."""

from functools import lru_cache
from typing import get_args

from pydantic import create_model

from api.schemas.tool import CreateToolRequest, ToolDefinition, UpdateToolRequest


@lru_cache(maxsize=12)
def tool_request_schema(category: str, operation: str = "create") -> dict:
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
    return {
        "category": category,
        "operation": operation,
        "schema": model.model_json_schema(),
    }
