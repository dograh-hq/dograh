"""Resolve transfer-call destinations from static config or HTTP resolvers."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from api.db import db_client
from api.services.organization_preferences import external_pbx_integrations_enabled
from api.services.workflow.tools.custom_tool import _resolve_preset_parameters
from api.utils.credential_auth import build_auth_header
from api.utils.template_renderer import render_template
from api.utils.url_security import validate_user_configured_service_url


@dataclass
class ResolvedTransferConfig:
    destination: str
    timeout_seconds: int
    message: Optional[str] = None
    source: str = "static"
    resolution_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class TransferResolutionError(ValueError):
    """Raised when a transfer destination cannot be resolved safely."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def _render_value(
    value: Any,
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
) -> str:
    initial_context = dict(call_context_vars or {})
    render_context: Dict[str, Any] = {
        **initial_context,
        "initial_context": initial_context,
        "gathered_context": dict(gathered_context_vars or {}),
    }
    rendered = render_template(value, render_context)
    if rendered is None:
        return ""
    return str(rendered).strip()


def _base_timeout(config: dict[str, Any]) -> int:
    timeout = config.get("timeout", 30)
    try:
        timeout_int = int(timeout)
    except (TypeError, ValueError):
        timeout_int = 30
    return min(max(timeout_int, 5), 120)


def _mask_destination(destination: Any) -> str:
    value = "" if destination is None else str(destination).strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return f"***{value[-4:]}"


_SENSITIVE_LOG_KEY_PARTS = (
    "authorization",
    "auth",
    "card",
    "destination",
    "email",
    "password",
    "phone",
    "secret",
    "ssn",
    "token",
)


def _safe_log_value(key: str, value: Any) -> Any:
    key_lower = key.lower()
    if any(part in key_lower for part in _SENSITIVE_LOG_KEY_PARTS):
        return "<redacted>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) > 80:
            return f"{stripped[:77]}..."
        return stripped
    if isinstance(value, list):
        return f"<array:{len(value)}>"
    if isinstance(value, dict):
        return f"<object:{len(value)}>"
    return f"<{type(value).__name__}>"


def _safe_log_dict(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        str(key): _safe_log_value(str(key), value)
        for key, value in (data or {}).items()
    }


def _resolve_static_transfer(
    config: dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
) -> ResolvedTransferConfig:
    return ResolvedTransferConfig(
        destination=_render_value(
            config.get("destination", ""), call_context_vars, gathered_context_vars
        ),
        timeout_seconds=_base_timeout(config),
    )


def _context_value(
    path: str,
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
) -> Any:
    initial = call_context_vars or {}
    gathered = gathered_context_vars or {}
    normalized = path.strip()
    if normalized.startswith("initial_context."):
        current: Any = initial
        parts = normalized.removeprefix("initial_context.").split(".")
    elif normalized.startswith("gathered_context."):
        current = gathered
        parts = normalized.removeprefix("gathered_context.").split(".")
    else:
        current = gathered
        parts = normalized.split(".")

    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    if current is None and "." not in normalized:
        extracted = gathered.get("extracted_variables")
        if isinstance(extracted, dict):
            current = extracted.get(normalized)
    return current


def _resolve_context_mapping_transfer(
    config: dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
) -> ResolvedTransferConfig:
    mapping = config.get("context_mapping")
    if not isinstance(mapping, dict):
        raise TransferResolutionError(
            "invalid_context_mapping", "Transfer context mapping is missing"
        )
    path = str(mapping.get("context_path", "")).strip()
    raw_value = _context_value(path, call_context_vars, gathered_context_vars)
    match_value = "" if raw_value is None else str(raw_value).strip().casefold()
    destination = ""
    for route in mapping.get("routes") or []:
        if not isinstance(route, dict):
            continue
        if str(route.get("context_value", "")).strip().casefold() == match_value:
            destination = str(route.get("destination", "")).strip()
            break
    if not destination:
        destination = str(mapping.get("fallback_destination") or "").strip()
    if not destination:
        raise TransferResolutionError(
            "no_context_mapping_match",
            f"No destination mapping matched gathered context path '{path}'",
        )
    return ResolvedTransferConfig(
        destination=destination,
        timeout_seconds=_base_timeout(config),
        source="context_mapping",
        metadata={"context_path": path, "matched": bool(match_value)},
    )


def _resolver_arguments(
    *,
    resolver: dict[str, Any],
    arguments: dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
) -> dict[str, Any]:
    try:
        preset_arguments = _resolve_preset_parameters(
            resolver, call_context_vars, gathered_context_vars
        )
    except ValueError as exc:
        raise TransferResolutionError("preset_parameter_error", str(exc)) from exc
    return {**(arguments or {}), **preset_arguments}


async def _execute_http_resolver(
    *,
    resolver: dict[str, Any],
    resolved_arguments: dict[str, Any],
    organization_id: Optional[int],
    resolution_id: str,
) -> dict[str, Any]:
    url = resolver.get("url", "")
    validate_user_configured_service_url(url, field_name="config.resolver.url")

    method = "POST"
    headers = dict(resolver.get("headers", {}) or {})
    if method in ("POST", "PUT", "PATCH"):
        headers.setdefault("Content-Type", "application/json")

    credential_uuid = resolver.get("credential_uuid")
    if credential_uuid and organization_id:
        credential = await db_client.get_credential_by_uuid(
            credential_uuid, organization_id
        )
        if credential:
            headers.update(build_auth_header(credential))
        else:
            raise TransferResolutionError(
                "credential_not_found",
                "Transfer resolver credential was not found for this organization",
            )

    body = resolved_arguments

    timeout_seconds = float(resolver.get("timeout_ms", 3000)) / 1000.0
    logger.debug(
        "Transfer resolver request prepared "
        f"resolution_id={resolution_id} method={method} "
        f"argument_keys={list(resolved_arguments.keys())} "
        f"arguments={_safe_log_dict(resolved_arguments)}"
    )

    try:
        started_at = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body,
            )
        duration_ms = int((time.monotonic() - started_at) * 1000)
    except httpx.TimeoutException as exc:
        raise TransferResolutionError(
            "resolver_timeout",
            f"Transfer resolver timed out after {timeout_seconds:.1f} seconds",
        ) from exc
    except httpx.RequestError as exc:
        raise TransferResolutionError(
            "resolver_request_failed", f"Transfer resolver request failed: {exc}"
        ) from exc

    if response.status_code < 200 or response.status_code >= 300:
        logger.warning(
            "Transfer resolver HTTP error "
            f"resolution_id={resolution_id} status_code={response.status_code} "
            f"duration_ms={duration_ms}"
        )
        raise TransferResolutionError(
            "resolver_http_error",
            f"Transfer resolver returned HTTP {response.status_code}",
        )

    try:
        data = response.json()
    except Exception as exc:
        raise TransferResolutionError(
            "invalid_resolver_response", "Transfer resolver returned non-JSON response"
        ) from exc

    if not isinstance(data, dict):
        raise TransferResolutionError(
            "invalid_resolver_response",
            "Transfer resolver response must be a JSON object",
        )
    logger.info(
        "Transfer resolver HTTP completed "
        f"resolution_id={resolution_id} status_code={response.status_code} "
        f"duration_ms={duration_ms} response_keys={list(data.keys())}"
    )
    return data


def _resolve_from_response(
    *,
    response_data: dict[str, Any],
    config: dict[str, Any],
    resolution_id: str,
) -> ResolvedTransferConfig:
    transfer_context = response_data.get("transfer_context")
    if not isinstance(transfer_context, dict):
        raise TransferResolutionError(
            "invalid_resolver_response",
            "Transfer resolver response must contain transfer_context object",
        )

    destination = transfer_context.get("destination")
    if not isinstance(destination, str) or not destination.strip():
        raise TransferResolutionError(
            "no_destination",
            "Transfer resolver response must contain transfer_context.destination",
        )

    custom_message = transfer_context.get("custom_message")
    if custom_message is not None and not isinstance(custom_message, str):
        raise TransferResolutionError(
            "invalid_custom_message",
            "transfer_context.custom_message must be a string when provided",
        )

    return ResolvedTransferConfig(
        destination=destination.strip(),
        timeout_seconds=_base_timeout(config),
        message=custom_message.strip() if custom_message else None,
        source="http_resolver",
        resolution_id=resolution_id,
    )


async def resolve_transfer_config(
    *,
    tool: Any,
    config: dict[str, Any],
    arguments: dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
    organization_id: Optional[int],
    workflow_run_id: Optional[int],
) -> ResolvedTransferConfig:
    """Resolve transfer destination and options for a transfer tool call."""

    destination_source = config.get("destination_source", "static")
    if destination_source == "context_mapping":
        if not organization_id or not await external_pbx_integrations_enabled(
            organization_id
        ):
            raise TransferResolutionError(
                "external_pbx_feature_disabled",
                "External PBX integrations are disabled for this organization",
            )
        resolved = _resolve_context_mapping_transfer(
            config, call_context_vars, gathered_context_vars
        )
        logger.info(
            "Transfer destination resolved from context mapping "
            f"context_path={resolved.metadata.get('context_path')} "
            f"source={resolved.source} destination={resolved.destination}"
        )
        return resolved

    resolver = config.get("resolver")
    if config.get("destination_source", "static") != "dynamic" or not isinstance(
        resolver, dict
    ):
        resolved = _resolve_static_transfer(
            config, call_context_vars, gathered_context_vars
        )
        logger.info(
            "Transfer destination resolved "
            f"source={resolved.source} destination={_mask_destination(resolved.destination)} "
            f"timeout={resolved.timeout_seconds}"
        )
        return resolved

    resolution_id = str(uuid.uuid4())
    logger.info(
        "Transfer resolver started "
        f"resolution_id={resolution_id} tool_uuid={getattr(tool, 'tool_uuid', None)} "
        f"workflow_run_id={workflow_run_id} type={resolver.get('type')} "
        "method=POST "
        f"timeout_ms={resolver.get('timeout_ms', 3000)}"
    )

    resolved_arguments = _resolver_arguments(
        resolver=resolver,
        arguments=arguments,
        call_context_vars=call_context_vars,
        gathered_context_vars=gathered_context_vars,
    )
    response_data = await _execute_http_resolver(
        resolver=resolver,
        resolved_arguments=resolved_arguments,
        organization_id=organization_id,
        resolution_id=resolution_id,
    )
    resolved = _resolve_from_response(
        response_data=response_data,
        config=config,
        resolution_id=resolution_id,
    )
    logger.info(
        "Transfer destination resolved "
        f"resolution_id={resolution_id} source={resolved.source} "
        f"destination={_mask_destination(resolved.destination)} "
        f"timeout={resolved.timeout_seconds} "
        f"custom_message_present={bool(resolved.message)}"
    )
    return resolved
