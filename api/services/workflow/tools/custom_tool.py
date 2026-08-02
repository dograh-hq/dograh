"""Custom tool execution for user-defined HTTP API tools."""

import json
import re
from typing import Any

import httpx
from loguru import logger

from api.db import db_client
from api.services.configuration.masking import mask_key
from api.utils.credential_auth import build_auth_header
from api.utils.template_renderer import render_template

# Map tool parameter types to JSON schema types
TYPE_MAP = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}


def validate_parameter_name(name: str) -> str:
    """Strip whitespace and ensure name is a valid template identifier."""
    if not name:
        return ""
    clean_name = name.strip()
    if not re.match(r"^[a-zA-Z0-9_\-]+$", clean_name):
        raise ValueError(
            f"Invalid parameter name '{name}'. Parameter names must contain only "
            "alphanumeric characters, underscores, and dashes."
        )
    return clean_name


# Matches a template leaf that is ENTIRELY one placeholder and nothing else.
# "{{adults}}" → matches;  "Ref-{{id}}" → does NOT match.
_WHOLE_PLACEHOLDER_RE = re.compile(r"^\{\{\s*([^|\s}]+)(?:\s*\|[^}]*)?\s*\}\}$")


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
        try:
            param_name = validate_parameter_name(param.get("name", ""))
        except ValueError:
            continue

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
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)):
            return value

        rendered = str(value).strip()
        if rendered == "":
            return None

        if rendered.lower() in ("true", "false"):
            return 1 if rendered.lower() == "true" else 0

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


def _coerce_typed_leaves(
    original_node: Any,
    rendered_node: Any,
    arguments: dict[str, Any],
    param_type_map: dict[str, str],
    call_context_vars: dict[str, Any] | None = None,
    gathered_context_vars: dict[str, Any] | None = None,
) -> Any:
    """Walk the original template and rendered output in parallel.

    If an original leaf is entirely a ``{{param_name}}`` placeholder, coerce the
    rendered string value back to the declared parameter type using the raw value
    from ``arguments``.

    Partial-placeholder strings (``"Ref-{{id}}"``) stay as strings.
    Non-string originals (int, bool, None) are returned from the rendered output
    unchanged — ``render_template`` already preserved their type (lines 89–91).
    """
    if isinstance(original_node, dict):
        if not isinstance(rendered_node, dict):
            return rendered_node
        if len(original_node) != len(rendered_node):
            # Key collision: two template keys rendered to the same string.
            # This would silently drop a field — raise so the call fails clearly.
            raise ValueError(
                "Body template contains keys that render to the same value. "
                "Use static keys or ensure placeholder keys resolve to unique strings."
            )

        return {
            rendered_key: _coerce_typed_leaves(
                orig_v,
                rendered_v,
                arguments,
                param_type_map,
                call_context_vars,
                gathered_context_vars,
            )
            for (_, orig_v), (rendered_key, rendered_v) in zip(
                original_node.items(), rendered_node.items()
            )
        }

    if isinstance(original_node, list):
        if not isinstance(rendered_node, list):
            return rendered_node
        # render_template preserves list length (line 87); zip is safe.
        return [
            _coerce_typed_leaves(
                orig_item,
                rend_item,
                arguments,
                param_type_map,
                call_context_vars,
                gathered_context_vars,
            )
            for orig_item, rend_item in zip(original_node, rendered_node)
        ]

    if isinstance(original_node, str):
        m = _WHOLE_PLACEHOLDER_RE.match(original_node)
        if m:
            param_name = m.group(1)

            if param_name.startswith("initial_context."):
                key_path = param_name[len("initial_context.") :]
                from api.utils.template_renderer import get_nested_value

                val = get_nested_value(call_context_vars or {}, key_path)
                # Only prefer raw context when non-empty; empty lets the fallback
                # filter in rendered_node (e.g. "{{phone | unknown}}") win.
                return val if val not in (None, "") else rendered_node

            if param_name.startswith("gathered_context."):
                key_path = param_name[len("gathered_context.") :]
                from api.utils.template_renderer import get_nested_value

                val = get_nested_value(gathered_context_vars or {}, key_path)
                return val if val not in (None, "") else rendered_node

            # Support dotted argument paths such as {{customer.age}} where
            # "customer" is an object parameter. Resolve via nested lookup first.
            if "." in param_name:
                from api.utils.template_renderer import get_nested_value

                raw_arg = get_nested_value(arguments, param_name)
                if raw_arg not in (None, ""):
                    return raw_arg

            # Flat LLM parameter — coerce to declared type if needed.
            declared_type = param_type_map.get(param_name, "string")
            if declared_type != "string":
                raw_arg = arguments.get(param_name)
                # If the raw argument is missing or empty, prefer the already-rendered
                # value (which incorporates any fallback filter, e.g. {{qty | 5}}).
                # Only attempt coercion when a non-empty raw argument exists.
                val_to_coerce = (
                    raw_arg
                    if (raw_arg is not None and raw_arg != "")
                    else rendered_node
                )
                try:
                    return _coerce_parameter_value(val_to_coerce, declared_type)
                except ValueError:
                    pass  # Leave as rendered string rather than crashing the call

    # Non-string originals and partial placeholders — already correct from render_template.
    return rendered_node


def render_body_template(
    template: dict[str, Any],
    arguments: dict[str, Any],
    parameters: list[dict[str, Any]],
    call_context_vars: dict[str, Any] | None = None,
    gathered_context_vars: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render a nested JSON body template using resolved arguments and call context.

    Args:
        template:              The body_template dict from the tool config.
        arguments:             Merged flat dict of LLM + preset resolved arguments.
                               Preset values already win (merged upstream).
        parameters:            Raw parameter dicts from config (each has "name", "type",
                               "required" keys — raw JSON dicts, NOT Pydantic instances).
        call_context_vars:     Call-start context for {{initial_context.*}} access.
        gathered_context_vars: Conversation context for {{gathered_context.*}} access.

    Returns:
        Fully rendered nested dict ready to be sent as the HTTP request body.

    Raises:
        ValueError: If a required parameter has no value.
    """
    # Build param name → declared type for post-render coercion.
    param_type_map: dict[str, str] = {}
    for p in parameters or []:
        try:
            clean_name = validate_parameter_name(p.get("name", ""))
            if clean_name:
                param_type_map[clean_name] = p.get("type", "string")
        except ValueError as e:
            raise ValueError(str(e))

    # Pre-render required parameter check (fast fail before any HTTP call).
    # 0, False, {}, [] are valid non-missing values — only None and "" trigger this.
    #
    # We intentionally avoid reusing workflow_graph.extract_template_variables here
    # because that function excludes system-injected vars (provider, campaign_id,
    # source_uuid). HTTP tool parameters may legitimately share those names and must
    # still be validated.
    import re as _re

    # Captures the top-level variable name before any dot path or fallback filter.
    # Matches {{name}}, {{name.path}}, {{name | fallback}}, {{name.path | fallback}}.
    _TMPL_VAR_RE = _re.compile(
        r"\{\{\s*([^.|\s}]+)(?:\.[^|\s}]+)*(?:\s*\|[^}]*)?\s*\}\}"
    )
    template_str = json.dumps(template) if template else "{}"
    # Collect all referenced top-level names (no fallback filter — those are optional by design).
    # Skip system dot-path variables like initial_context.x and gathered_context.x.
    _SYSTEM_PREFIXES = {
        "initial_context",
        "gathered_context",
        "current_time",
        "current_weekday",
    }
    required_in_template: set[str] = {
        m.group(1)
        for m in _TMPL_VAR_RE.finditer(template_str)
        if "|"
        not in template_str[
            m.start() : m.end()
        ]  # skip fallback vars (they're optional)
        and m.group(1) not in _SYSTEM_PREFIXES  # skip system-injected prefixes
    }
    _TMPL_PATH_RE = _re.compile(r"\{\{\s*([^|\s}]+\.[^|\s}]+)\s*\}\}")
    required_dotted_paths: set[str] = {
        m.group(1)
        for m in _TMPL_PATH_RE.finditer(template_str)
        if "|" not in template_str[m.start() : m.end()]
        and m.group(1).split(".", 1)[0] not in _SYSTEM_PREFIXES
    }

    def _get_dotted_val(d: dict[str, Any], path: str) -> Any:
        curr: Any = d
        for part in path.split("."):
            if isinstance(curr, dict) and part in curr:
                curr = curr[part]
            else:
                return None
        return curr

    for param in parameters or []:
        try:
            name = validate_parameter_name(param.get("name", ""))
        except ValueError:
            continue
        if not name:
            continue
        param_type = param.get("type", "string")
        if param.get("required", True):
            if name in required_in_template:
                val = arguments.get(name)
                if val is None or val == "":
                    raise ValueError(
                        f"Required parameter '{name}' has no value. "
                        "The agent must collect this before calling the tool."
                    )
            # Enforce strict dotted-path existence for all parameters, including objects,
            # so incomplete payloads never leave the service.
            for dotted_path in required_dotted_paths:
                if dotted_path.startswith(f"{name}."):
                    sub_val = _get_dotted_val(arguments, dotted_path)
                    if sub_val is None or sub_val == "":
                        raise ValueError(
                            f"Required parameter path '{dotted_path}' has no value. "
                            "The agent must collect this before calling the tool."
                        )

    # Build render context.
    #
    # KEY ORDERING: nested framework keys are placed FIRST so that **arguments
    # (spread LAST) wins for any flat key collision. This guarantees LLM/preset
    # values are never silently overwritten by call context keys.
    #
    # "initial_context" and "gathered_context" are RESERVED names; if an LLM
    # parameter uses either name, **arguments will overwrite the nested dict and
    # {{initial_context.*}} / {{gathered_context.*}} path lookups will return "".
    # The UI warns and blocks saving when reserved names are detected.
    #
    # We do NOT spread **call_context_vars flat (unlike _resolve_preset_parameters).
    # In preset templates, that flat spread is safe because no LLM args are present.
    # Here, LLM args ARE present; a flat spread could silently clobber LLM values.

    safe_arguments = dict(arguments)
    # Guard against tools that bypass schema validation (e.g. pre-existing stored
    # config, MCP-authored tools) and carry a parameter literally named
    # "initial_context" or "gathered_context". If such a value reached render_context
    # it would silently clobber the namespace dict and corrupt all {{initial_context.*}}
    # lookups. Raise explicitly so the failure is surfaced cleanly.
    _RESERVED_NAMESPACES = {"initial_context", "gathered_context"}
    for reserved in _RESERVED_NAMESPACES:
        if reserved in safe_arguments and reserved in param_type_map:
            raise ValueError(
                f"Tool parameter '{reserved}' conflicts with a reserved Dograh namespace. "
                "Rename the parameter in the tool configuration."
            )

    render_context: dict[str, Any] = {
        **safe_arguments,  # LLM + preset values FIRST
        "initial_context": dict(call_context_vars or {}),
        "gathered_context": dict(gathered_context_vars or {}),
    }

    # Render all {{placeholders}}.
    rendered = render_template(template, render_context)
    if not isinstance(rendered, dict):
        # Defensive: body_template is schema-validated to be a dict, and
        # render_template(dict, ...) always returns a dict (lines 76–83).
        raise ValueError("Rendered body template is not a JSON object.")

    # Restore correct types for whole-placeholder number/boolean/object/array leaves.
    return _coerce_typed_leaves(
        template,
        rendered,
        arguments,
        param_type_map,
        call_context_vars=call_context_vars,
        gathered_context_vars=gathered_context_vars,
    )


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
        try:
            param_name = validate_parameter_name(param.get("name", ""))
        except ValueError as e:
            raise ValueError(str(e))

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

    # Initialize BEFORE build_result is defined.
    # The closure captures _body_preview by reference (cell object).
    # By the time build_result is first called (line 316 preset error path),
    # _body_preview must already be assigned.
    _body_preview: dict[str, Any] | None = None

    def build_result(result: dict[str, Any]) -> dict[str, Any]:
        if include_request_headers:
            # Both extras are test-mode-only: include_request_headers=True is set
            # exclusively by the test endpoint (routes/tool.py:244). Live pipecat
            # calls (pipecat_engine_custom_tools.py:666) never set this flag, so
            # the LLM callback never receives request_headers or request_body_preview.
            return {
                **result,
                "request_headers": request_headers,
                "request_body_preview": _body_preview,  # reads cell at call time
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
            # _body_preview = None at this point (assigned before build_result). ✓
    else:
        preset_arguments = dict(preset_params)

    resolved_arguments = {**(arguments or {}), **preset_arguments}

    # Build request body or query params.
    body = None
    params = None
    body_template = config.get("body_template")

    if method in ("POST", "PUT", "PATCH"):
        if body_template is not None:
            parameters = [
                *(config.get("parameters") or []),
                *(config.get("preset_parameters") or []),
            ]
            try:
                body = render_body_template(
                    template=body_template,
                    arguments=resolved_arguments,
                    parameters=parameters,
                    call_context_vars=call_context_vars,
                    gathered_context_vars=gathered_context_vars,
                )
            except Exception as e:
                logger.error(
                    f"Custom tool '{tool.name}' body template render failed: {e}"
                )
                return build_result(
                    {
                        "status": "error",
                        "error": f"Body template rendering failed: {e!s}",
                    }
                )
                # _body_preview is still None here (set below, after this block). ✓
        else:
            body = resolved_arguments  # flat mode — unchanged behaviour

    elif method in ("GET", "DELETE") and resolved_arguments:
        params = serialize_query_params(resolved_arguments)

    # Capture final body for the test-mode preview.
    # The closure reads _body_preview at call time, after this assignment.
    _body_preview = body

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
