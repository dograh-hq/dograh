"""Template rendering utility with support for nested JSON paths."""

import json
import re
from datetime import datetime
from typing import Any, Dict, Optional, Union
from zoneinfo import ZoneInfo

from loguru import logger

from api.services.workflow.workflow_graph import TEMPLATE_VAR_PATTERN

_CURRENT_TIME_PREFIX = "current_time"
_CURRENT_WEEKDAY_PREFIX = "current_weekday"


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


def _extract_timezone_from_template(template_str: str) -> Optional[str]:
    """Extract the timezone from a ``current_time_<TZ>`` or ``current_weekday_<TZ>`` variable.

    Returns the first IANA timezone found, or None.
    """
    pattern = (
        r"\{\{\s*(?:"
        + re.escape(_CURRENT_TIME_PREFIX)
        + r"|"
        + re.escape(_CURRENT_WEEKDAY_PREFIX)
        + r")_([^|\s}]+)"
    )
    match = re.search(pattern, template_str)
    return match.group(1).strip() if match else None


def _resolve_builtin_variable(
    variable_path: str, default_tz: Optional[str] = None
) -> Optional[str]:
    """Resolve built-in template variables that are available in all contexts.

    Supported variables:
        - ``current_time`` – current time in UTC
        - ``current_time_<TIMEZONE>`` – current time in the given IANA timezone
        - ``current_weekday`` – current weekday name (uses *default_tz* if set, else UTC)
        - ``current_weekday_<TIMEZONE>`` – current weekday name in the given timezone

    Args:
        variable_path: The template variable name to resolve.
        default_tz: Fallback timezone for ``current_weekday`` when no explicit
            timezone suffix is provided (typically inferred from a
            ``current_time_<TZ>`` variable in the same template).

    Returns:
        The resolved string value, or None if *variable_path* is not a
        recognised built-in.
    """
    if variable_path == _CURRENT_TIME_PREFIX:
        tz = ZoneInfo(default_tz) if default_tz else ZoneInfo("UTC")
        return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

    if variable_path.startswith(_CURRENT_TIME_PREFIX + "_"):
        timezone = variable_path[len(_CURRENT_TIME_PREFIX) + 1 :]
        try:
            return datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            logger.warning(f"Invalid timezone in template variable: {timezone}")
            return None

    if variable_path == _CURRENT_WEEKDAY_PREFIX:
        tz = ZoneInfo(default_tz) if default_tz else ZoneInfo("UTC")
        return datetime.now(tz).strftime("%A")

    if variable_path.startswith(_CURRENT_WEEKDAY_PREFIX + "_"):
        timezone = variable_path[len(_CURRENT_WEEKDAY_PREFIX) + 1 :]
        try:
            return datetime.now(ZoneInfo(timezone)).strftime("%A")
        except Exception:
            logger.warning(f"Invalid timezone in template variable: {timezone}")
            return None

    return None


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

    # Pre-scan for a current_time_<TZ> variable so that {{current_weekday}}
    # can inherit the same timezone instead of defaulting to UTC.
    default_tz = _extract_timezone_from_template(template_str)

    def _replace(match: re.Match[str]) -> str:  # type: ignore[type-arg]
        variable_path = match.group(1).strip()
        filter_name = match.group(2).strip() if match.group(2) else None
        filter_value = match.group(3).strip() if match.group(3) else None

        # Check for built-in variables first (current_time, current_weekday)
        builtin_value = _resolve_builtin_variable(variable_path, default_tz)
        if builtin_value is not None:
            return builtin_value

        # Get value using nested path lookup
        value = get_nested_value(context, variable_path)

        # Apply fallback: new syntax {{var | default}} or legacy {{var | fallback:default}}
        if filter_name is not None:
            if value is None or value == "":
                if filter_name == "fallback":
                    # Legacy syntax: {{var | fallback:default}}
                    value = (
                        filter_value
                        if filter_value is not None
                        else variable_path.title()
                    )
                else:
                    # New syntax: {{var | default}}
                    value = filter_name

        # Convert to string for substitution
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    # Replace template variables
    result = re.sub(TEMPLATE_VAR_PATTERN, _replace, template_str)

    # Handle line breaks (convert literal \n to actual newlines)
    result = result.replace("\\n", "\n")

    return result
