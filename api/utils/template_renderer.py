"""Template rendering utility with support for nested JSON paths."""

import json
import re
from typing import Any, Dict, Union


def get_nested_value(obj: Any, path: str) -> Any:
    """
    Get a nested value from a dictionary using dot notation.

    Args:
        obj: The object to traverse (dict or any)
        path: Dot-separated path (e.g., "a.b.c")

    Returns:
        The value at the path, or None if not found

    Examples:
        get_nested_value({"a": {"b": 1}}, "a.b") -> 1
        get_nested_value({"a": {"b": {"c": 2}}}, "a.b.c") -> 2
        get_nested_value({"a": 1}, "a.b") -> None
    """
    if not path:
        return obj

    keys = path.split(".")
    current = obj

    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None

        if current is None:
            return None

    return current


def render_template(
    template: Union[str, dict, list, None],
    context: Dict[str, Any],
) -> Union[str, dict, list, None]:  # noqa: C901 – complex but self-contained
    """
    Render a template with variable substitution supporting nested paths.

    Supports:
    - String templates: "Hello {{name}}"
    - JSON templates: {"key": "{{value}}"}
    - Nested paths: "{{initial_context.phone_number}}"
    - Deep nesting: "{{gathered_context.customer.address.city}}"
    - Fallback: "{{name | fallback:Unknown}}"

    Args:
        template: String, dict, list, or None with {{variable}} placeholders
        context: Dict containing all available variables

    Returns:
        Rendered template with variables replaced
    """
    if template is None:
        return None

    # Handle dict templates recursively
    if isinstance(template, dict):
        return {
            _render_string(str(k), context)
            if isinstance(k, str)
            else k: render_template(v, context)
            for k, v in template.items()
        }

    # Handle list templates recursively
    if isinstance(template, list):
        return [render_template(item, context) for item in template]

    # Handle non-string types (int, float, bool, etc.)
    if not isinstance(template, str):
        return template

    return _render_string(template, context)


def _render_string(template_str: str, context: Dict[str, Any]) -> str:
    """
    Render a string template with variable substitution.

    Args:
        template_str: String with {{variable}} placeholders
        context: Dict containing all available variables

    Returns:
        Rendered string with variables replaced
    """
    if not template_str:
        return template_str

    # Pattern: {{ path }} or {{ path | filter }} or {{ path | filter:default }}
    pattern = r"\{\{\s*([^|\s}]+)(?:\s*\|\s*([^:}]+)(?::([^}]+))?)?\s*\}\}"

    def _replace(match: re.Match[str]) -> str:  # type: ignore[type-arg]
        variable_path = match.group(1).strip()
        filter_name = match.group(2).strip() if match.group(2) else None
        filter_value = match.group(3).strip() if match.group(3) else None

        # Get value using nested path lookup
        value = get_nested_value(context, variable_path)

        # Apply filters
        if filter_name == "fallback":
            if value is None or value == "":
                value = (
                    filter_value if filter_value is not None else variable_path.title()
                )

        # Convert to string for substitution
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    # Replace template variables
    result = re.sub(pattern, _replace, template_str)

    # Handle line breaks (convert literal \n to actual newlines)
    result = result.replace("\\n", "\n")

    return result
