"""Custom tool execution for user-defined HTTP API tools."""

import json
import re
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from api.db import db_client
from api.services.configuration.masking import mask_key
from api.services.workflow.workflow_graph import TEMPLATE_VAR_PATTERN
from api.utils.credential_auth import build_auth_header
from api.utils.template_renderer import (
    _extract_timezone_from_template,
    _resolve_builtin_variable,
    get_nested_value,
    render_template,
)
from loguru import logger

# Map tool parameter types to JSON schema types
TYPE_MAP = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}


def custom_tool_function_name(name: str) -> str:
    """Return the LLM function name generated for a custom tool."""
    function_name = re.sub(r"[^a-z0-9_]", "_", name.lower())
    return re.sub(r"_+", "_", function_name).strip("_")


def serialize_query_params(arguments: dict[str, Any]) -> dict[str, Any]:
    """JSON-stringify dict/list values so they're safe to pass as query params.

    httpx (and query strings in general) only support primitive param values.
    Object/array-typed tool arguments must be serialized before going out as
    GET/DELETE query params, otherwise httpx raises a TypeError.
    """
    return {
        k: json.dumps(v) if isinstance(v, (dict, list)) else v
        for k, v in arguments.items()
    }


def tool_to_function_schema(tool: Any) -> dict[str, Any]:
    """Convert a ToolModel to an LLM function schema.

    Args:
        tool: ToolModel instance with name, description, and definition

    Returns:
        Function schema dict compatible with OpenAI/Anthropic function calling
    """
    definition = tool.definition or {}
    config = definition.get("config", {})
    parameters = config.get("parameters", []) or []
    if (
        definition.get("type") == "transfer_call"
        and config.get("destination_source", "static") != "dynamic"
    ):
        parameters = []
    elif (
        definition.get("type") == "transfer_call"
        and config.get("destination_source", "static") == "dynamic"
    ):
        resolver = config.get("resolver")
        if isinstance(resolver, dict):
            parameters = resolver.get("parameters", []) or []
        else:
            parameters = []

    # Build properties and required list from parameters
    properties = {}
    required = []

    for param in parameters:
        param_name = param.get("name", "")
        param_type = param.get("type", "string")
        param_desc = param.get("description", "")
        param_required = param.get("required", True)

        if not param_name:
            continue

        schema_type = TYPE_MAP.get(param_type, "string")
        if schema_type == "object":
            properties[param_name] = {
                "type": "object",
                "additionalProperties": True,
                "description": param_desc,
            }
        elif schema_type == "array":
            properties[param_name] = {
                "type": "array",
                "items": {},
                "description": param_desc,
            }
        else:
            properties[param_name] = {
                "type": schema_type,
                "description": param_desc,
            }

        if param_required:
            required.append(param_name)

    # If this is an end_call tool with endCallReason enabled, add a required 'reason' parameter
    if definition.get("type") == "end_call" and config.get("endCallReason", False):
        default_description = (
            "The reason for ending the call (e.g., 'voicemail_detected', "
            "'issue_resolved', 'customer_requested')"
        )
        properties["reason"] = {
            "type": "string",
            "description": config.get("endCallReasonDescription")
            or default_description,
        }
        required.append("reason")

    function_name = custom_tool_function_name(tool.name)

    return {
        "type": "function",
        "function": {
            "name": function_name,
            "description": tool.description or f"Execute {tool.name} tool",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
        "_tool_uuid": tool.tool_uuid,
    }


def _coerce_parameter_value(value: Any, param_type: str) -> Any:
    """Coerce a rendered preset parameter into the configured JSON type."""

    if value is None:
        return None

    if param_type == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    if param_type == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value

        rendered = str(value).strip()
        if rendered == "":
            return None

        if re.fullmatch(r"[-+]?\d+", rendered):
            return int(rendered)

        return float(rendered)

    if param_type == "boolean":
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        rendered = str(value).strip().lower()
        if rendered in {"true", "1", "yes", "y", "on"}:
            return True
        if rendered in {"false", "0", "no", "n", "off"}:
            return False

        raise ValueError(f"Cannot convert '{value}' to boolean")

    if param_type == "object":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Cannot convert '{value}' to object") from exc
        if isinstance(value, dict):
            return value
        raise ValueError(f"Cannot convert '{value}' to object")

    if param_type == "array":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Cannot convert '{value}' to array") from exc
        if isinstance(value, list):
            return value
        raise ValueError(f"Cannot convert '{value}' to array")

    return value


def _resolve_preset_parameters(
    config: dict[str, Any],
    call_context_vars: dict[str, Any] | None,
    gathered_context_vars: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve fixed/template-backed parameters before executing the HTTP request."""

    preset_parameters = config.get("preset_parameters", []) or []
    if not preset_parameters:
        return {}

    initial_context = dict(call_context_vars or {})
    render_context: dict[str, Any] = {
        **initial_context,
        "initial_context": initial_context,
        "gathered_context": dict(gathered_context_vars or {}),
    }

    resolved: dict[str, Any] = {}
    for param in preset_parameters:
        param_name = (param.get("name") or "").strip()
        if not param_name:
            continue

        rendered = render_template(param.get("value_template", ""), render_context)
        if rendered in (None, ""):
            if param.get("required", True):
                raise ValueError(
                    f"Preset parameter '{param_name}' resolved to an empty value"
                )
            continue

        resolved[param_name] = _coerce_parameter_value(
            rendered, param.get("type", "string")
        )

    return resolved



def render_url_template(
    url: str,
    resolved_arguments: dict[str, Any],
    call_context_vars: dict[str, Any] | None,
    gathered_context_vars: dict[str, Any] | None,
    param_type_map: dict[str, str],
) -> tuple[str, set[str]]:
    """Render {{placeholders}} in a URL string, percent-encoding each substituted value.

    Returns:
        (rendered_url, consumed_param_names)

    Raises:
        ValueError: If any required {{placeholder}} is unresolved, or template is malformed.
    """

    # Fast malformed checks
    if url.count("{{") != url.count("}}"):
        if url.count("{{") > url.count("}}"):
            raise ValueError("Malformed URL template: unmatched '{{' found.")
        else:
            raise ValueError("Malformed URL template: unmatched '}}' found.")

    if "{{{{" in url:
        raise ValueError("Malformed URL template: nested '{{' found.")

    if not url or "{{" not in url:
        return url, set()

    consumed_params = set()
    default_tz = _extract_timezone_from_template(url)

    def _replace(match: re.Match[str]) -> str:
        variable_path = match.group(1).strip()
        filter_name = match.group(2).strip() if match.group(2) else None
        filter_value = match.group(3).strip() if match.group(3) else None

        builtin_value = _resolve_builtin_variable(variable_path, default_tz)
        if builtin_value is not None:
            return quote(builtin_value, safe="")

        if variable_path.startswith("initial_context."):
            key_path = variable_path[len("initial_context.") :]
            val = get_nested_value(call_context_vars or {}, key_path)
        elif variable_path.startswith("gathered_context."):
            key_path = variable_path[len("gathered_context.") :]
            val = get_nested_value(gathered_context_vars or {}, key_path)
        else:
            base_param_name = variable_path.split(".")[0]
            if base_param_name not in param_type_map:
                raise ValueError(
                    f"Undeclared URL placeholder: '{base_param_name}' is not a configured parameter."
                )

            param_type = param_type_map[base_param_name]
            if param_type == "array":
                raise ValueError(
                    "Array parameters cannot be used as URL path parameters."
                )
            if param_type == "object" and "." not in variable_path:
                raise ValueError(
                    "Object parameters cannot be used as URL path parameters directly."
                )

            consumed_params.add(base_param_name)
            val = get_nested_value(resolved_arguments, variable_path)

        # Fallback filter handling
        if filter_name is not None:
            if val is None or val == "":
                if filter_name == "fallback":
                    val = (
                        filter_value
                        if filter_value is not None
                        else variable_path.title()
                    )
                else:
                    val = filter_name if filter_name != "default" else ""

        if val is None:
            raise ValueError(
                f"URL path parameter '{variable_path}' has no value. The agent must collect this before calling the tool."
            )
        if val == "" and filter_name is None:
            raise ValueError(
                f"URL path parameter '{variable_path}' resolved to an empty string."
            )

        if isinstance(val, bool):
            val = str(val).lower()
        else:
            val = str(val)

        return quote(val, safe="")

    rendered_url = re.sub(TEMPLATE_VAR_PATTERN, _replace, url)
    
    if "{{" in rendered_url or "}}" in rendered_url:
        raise ValueError("Malformed URL template: invalid placeholder syntax.")
        
    original_parsed = urlparse(url)
    rendered_parsed = urlparse(rendered_url)
    if original_parsed.scheme != rendered_parsed.scheme or original_parsed.netloc != rendered_parsed.netloc:
        raise ValueError("URL placeholders cannot alter the scheme or host of the configured endpoint.")
        
    return rendered_url, consumed_params


async def execute_http_tool(
    tool: Any,
    arguments: dict[str, Any],
    call_context_vars: dict[str, Any] | None = None,
    gathered_context_vars: dict[str, Any] | None = None,
    preset_params: dict[str, Any] | None = None,
    organization_id: int | None = None,
    include_request_headers: bool = False,
) -> dict[str, Any]:
    """Execute an HTTP API tool.

    Args:
        tool: ToolModel instance
        arguments: Arguments passed by the LLM (parameter name -> value)
        call_context_vars: Initial context variables available at runtime
        gathered_context_vars: Variables extracted during the conversation
        preset_params: Pre-resolved preset parameter values. Used by the test
            endpoint; live calls omit this so configured templates are resolved.
        organization_id: Organization ID for credential lookup
        include_request_headers: Include a client-safe header preview in the result.
            Headers supplied by a stored credential are masked.

    Returns:
        Result dict with response data or error
    """
    definition = tool.definition or {}
    config = definition.get("config", {})

    # Get HTTP method and URL
    method = config.get("method", "POST").upper()
    url = config.get("url", "")

    # Get headers from config
    headers = dict(config.get("headers", {}) or {})

    # Add auth header if credential is configured. Keep track of which headers
    # came from the credential so only those values are masked in test previews.
    credential_headers: dict[str, str] = {}
    credential_uuid = config.get("credential_uuid")
    if credential_uuid and organization_id:
        try:
            credential = await db_client.get_credential_by_uuid(
                credential_uuid, organization_id
            )
            if credential:
                credential_headers = build_auth_header(credential)
                headers.update(credential_headers)
                logger.debug(f"Applied credential '{credential.name}' to tool request")
            else:
                logger.warning(
                    f"Credential {credential_uuid} not found for tool '{tool.name}'"
                )
        except Exception as e:
            logger.error(f"Failed to fetch credential for tool '{tool.name}': {e}")

    request_headers: dict[str, str] = {}
    if include_request_headers:
        request_headers = {str(name): str(value) for name, value in headers.items()}
        for header_name, header_value in credential_headers.items():
            request_headers[header_name] = mask_key(str(header_value))

    _rendered_url: str | None = None
    _url_consumed: set[str] = set()

    def build_result(result: dict[str, Any]) -> dict[str, Any]:
        if include_request_headers:
            return {
                **result,
                "request_headers": request_headers,
                "rendered_url": _rendered_url,
                "consumed_path_params": list(_url_consumed),
            }
        return result

    # Get timeout
    timeout_ms = config.get("timeout_ms", 5000)
    timeout_seconds = timeout_ms / 1000

    if preset_params is None:
        try:
            preset_arguments = _resolve_preset_parameters(
                config, call_context_vars, gathered_context_vars
            )
        except ValueError as e:
            logger.error(f"Custom tool '{tool.name}' preset parameter error: {e}")
            return build_result({"status": "error", "error": str(e)})
    else:
        preset_arguments = dict(preset_params)

    resolved_arguments = {**(arguments or {}), **preset_arguments}

    parameters = [
        *(config.get("parameters") or []),
        *(config.get("preset_parameters") or []),
    ]
    param_type_map: dict[str, str] = {}
    for p in parameters:
        clean_name = p.get("name", "")
        if clean_name:
            param_type_map[clean_name] = p.get("type", "string")

    try:
        url, _url_consumed_res = render_url_template(
            url=url,
            resolved_arguments=resolved_arguments,
            call_context_vars=call_context_vars,
            gathered_context_vars=gathered_context_vars,
            param_type_map=param_type_map,
        )
        _rendered_url = url
        _url_consumed = _url_consumed_res

        query_arguments = {
            k: v for k, v in resolved_arguments.items() if k not in _url_consumed
        }
    except ValueError as e:
        logger.error(f"Custom tool '{tool.name}' URL template render failed: {e}")
        return build_result(
            {"status": "error", "error": f"URL template rendering failed: {e!s}"}
        )

    # Build request: JSON body for POST/PUT/PATCH, query params for GET/DELETE
    body = None
    params = None
    if method in ("POST", "PUT", "PATCH"):
        body = query_arguments
    elif method in ("GET", "DELETE") and query_arguments:
        params = serialize_query_params(query_arguments)

    logger.info(
        f"Executing custom tool '{tool.name}' ({tool.tool_uuid}): {method} {url}"
    )
    if preset_arguments:
        logger.debug(
            f"Resolved preset parameters for '{tool.name}': {list(preset_arguments.keys())}"
        )
    logger.debug(f"Request body: {body}, params: {params}")

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body,
                params=params,
            )

            # Try to parse JSON response
            try:
                response_data = response.json()
            except Exception:
                response_data = {"raw_response": response.text}

            result = {
                "status": "success",
                "status_code": response.status_code,
                "data": response_data,
            }

            logger.debug(
                f"Custom tool '{tool.name}' completed with status {response.status_code}"
            )
            return build_result(result)

    except httpx.TimeoutException:
        logger.error(f"Custom tool '{tool.name}' timed out after {timeout_seconds}s")
        return build_result(
            {
                "status": "error",
                "error": f"Request timed out after {timeout_seconds} seconds",
            }
        )
    except httpx.RequestError as e:
        logger.error(f"Custom tool '{tool.name}' request failed: {e}")
        return build_result(
            {
                "status": "error",
                "error": f"Request failed: {e!s}",
            }
        )
    except Exception as e:
        logger.error(f"Custom tool '{tool.name}' execution failed: {e}")
        return build_result(
            {
                "status": "error",
                "error": f"Tool execution failed: {e!s}",
            }
        )
