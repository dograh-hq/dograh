"""Template rendering utility with support for nested JSON paths."""

import json
import re
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Union
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from loguru import logger

from api.services.workflow.workflow_graph import TEMPLATE_VAR_PATTERN

_CURRENT_TIME_PREFIX = "current_time"
_CURRENT_WEEKDAY_PREFIX = "current_weekday"
_INITIAL_CONTEXT_PREFIX = "initial_context."


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


def _resolve_template_value(
    variable_path: str,
    filter_name: Optional[str],
    filter_value: Optional[str],
    context: Dict[str, Any],
    default_tz: Optional[str],
) -> Any:
    """Resolve one parsed template variable using the shared template semantics."""

    builtin_value = _resolve_builtin_variable(variable_path, default_tz)
    if builtin_value is not None:
        return builtin_value

    # Prompts commonly reference initial_context.<key>, while some runtime
    # callers pass the initial context itself as the render context.
    value = get_nested_value(context, variable_path)
    if value is None and variable_path.startswith(_INITIAL_CONTEXT_PREFIX):
        value = get_nested_value(context, variable_path[len(_INITIAL_CONTEXT_PREFIX) :])

    # Apply fallback: new syntax {{var | default}} or legacy
    # {{var | fallback:default}}.
    if filter_name is not None and value in (None, ""):
        if filter_name == "fallback":
            return filter_value if filter_value is not None else variable_path.title()
        return filter_name

    return value


def _render_string(
    template_str: str,
    context: Dict[str, Any],
    *,
    value_formatter: Optional[Callable[[str, Any, Optional[str]], str]] = None,
    replace_escaped_newlines: bool = True,
) -> str:
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

        value = _resolve_template_value(
            variable_path,
            filter_name,
            filter_value,
            context,
            default_tz,
        )

        if value_formatter is not None:
            return value_formatter(variable_path, value, filter_name)

        # Convert to string for substitution
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    # Replace template variables
    result = re.sub(TEMPLATE_VAR_PATTERN, _replace, template_str)

    # Handle line breaks (convert literal \n to actual newlines)
    if replace_escaped_newlines:
        result = result.replace("\\n", "\n")

    return result


def render_url_template(
    url: str,
    context: Dict[str, Any],
) -> str:
    """Render URL placeholders with shared template semantics and URL safety.

    Values are resolved by the same logic used by :func:`render_template`, then
    percent-encoded before substitution. Template variables may be used in the
    hostname and path, but the configured URL scheme must remain unchanged.
    """

    if url.count("{{") != url.count("}}"):
        if url.count("{{") > url.count("}}"):
            raise ValueError("Malformed URL template: unmatched '{{' found.")
        raise ValueError("Malformed URL template: unmatched '}}' found.")

    if "{{{{" in url:
        raise ValueError("Malformed URL template: nested '{{' found.")

    if not url or "{{" not in url:
        return url

    def _format_url_value(
        variable_path: str,
        value: Any,
        filter_name: Optional[str],
    ) -> str:
        if value is None:
            raise ValueError(f"URL template variable '{variable_path}' has no value.")
        if value == "" and filter_name is None:
            raise ValueError(
                f"URL template variable '{variable_path}' resolved to an empty string."
            )
        if isinstance(value, list):
            raise ValueError("Arrays cannot be rendered directly into a URL.")
        if isinstance(value, dict):
            raise ValueError("Objects cannot be rendered directly into a URL.")

        rendered_value = str(value).lower() if isinstance(value, bool) else str(value)
        return quote(rendered_value, safe="")

    rendered_url = _render_string(
        url,
        context,
        value_formatter=_format_url_value,
        replace_escaped_newlines=False,
    )

    if "{{" in rendered_url or "}}" in rendered_url:
        raise ValueError("Malformed URL template: invalid placeholder syntax.")

    original_parsed = urlparse(url)
    rendered_parsed = urlparse(rendered_url)
    if original_parsed.scheme != rendered_parsed.scheme:
        raise ValueError(
            "URL placeholders cannot alter the scheme of the configured endpoint."
        )

    try:
        rendered_hostname = rendered_parsed.hostname
    except ValueError as exc:
        raise ValueError("URL template rendered an invalid hostname.") from exc

    if rendered_parsed.scheme not in {"http", "https"} or not rendered_hostname:
        raise ValueError("URL template must render to a valid HTTP or HTTPS URL.")

    return rendered_url
