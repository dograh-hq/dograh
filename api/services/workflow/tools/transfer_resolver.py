"""Resolve transfer-call destinations from static config or dynamic resolvers."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from api.db import db_client
from api.utils.credential_auth import build_auth_header
from api.utils.template_renderer import render_template
from api.utils.url_security import validate_user_configured_service_url


@dataclass
class ResolvedTransferConfig:
    destination: str
    timeout_seconds: int
    message: Optional[str] = None
    route: Optional[str] = None
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


def _mask_destination(destination: Any) -> str:
    value = _normalize_destination_value(destination)
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return f"***{value[-4:]}"


def _safe_log_value(value: Any) -> Any:
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
    return {str(key): _safe_log_value(value) for key, value in (data or {}).items()}


def _base_timeout(config: dict[str, Any]) -> int:
    timeout = config.get("timeout", 30)
    try:
        timeout_int = int(timeout)
    except (TypeError, ValueError):
        timeout_int = 30
    return min(max(timeout_int, 5), 120)


def _normalize_destination_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        return ""
    return str(value).strip()


def _resolve_static_transfer(
    config: dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
    *,
    resolution_id: Optional[str] = None,
    source: str = "static",
) -> ResolvedTransferConfig:
    return ResolvedTransferConfig(
        destination=_render_value(
            config.get("destination", ""), call_context_vars, gathered_context_vars
        ),
        timeout_seconds=_base_timeout(config),
        source=source,
        resolution_id=resolution_id,
    )


def _expand_approved_route(
    *,
    route_key: str,
    config: dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
    resolution_id: Optional[str] = None,
    source: str = "approved_route",
) -> ResolvedTransferConfig:
    approved_routes = config.get("approved_routes") or {}
    if not isinstance(approved_routes, dict) or route_key not in approved_routes:
        raise TransferResolutionError(
            "unknown_route", f"Resolver returned unknown transfer route '{route_key}'"
        )

    route = approved_routes[route_key] or {}
    if not isinstance(route, dict):
        raise TransferResolutionError(
            "invalid_route", f"Transfer route '{route_key}' is not configured correctly"
        )

    destination = _render_value(
        route.get("destination", ""), call_context_vars, gathered_context_vars
    )
    timeout = route.get("timeout_seconds")
    if timeout is None:
        timeout = _base_timeout(config)
    try:
        timeout_int = int(timeout)
    except (TypeError, ValueError) as exc:
        raise TransferResolutionError(
            "invalid_timeout", f"Transfer route '{route_key}' has invalid timeout"
        ) from exc

    return ResolvedTransferConfig(
        destination=destination,
        timeout_seconds=min(max(timeout_int, 5), 120),
        message=route.get("message"),
        route=route_key,
        source=source,
        resolution_id=resolution_id,
        metadata=dict(route.get("metadata") or {}),
    )


async def _execute_http_resolver(
    *,
    resolver: dict[str, Any],
    tool: Any,
    arguments: dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
    organization_id: Optional[int],
    workflow_run_id: Optional[int],
    resolution_id: str,
) -> dict[str, Any]:
    url = resolver.get("url", "")
    validate_user_configured_service_url(url, field_name="config.resolver.url")

    headers = {"Content-Type": "application/json"}
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

    payload = {
        "event": "transfer_resolution_requested",
        "workflow_run_id": workflow_run_id,
        "tool": {
            "tool_uuid": getattr(tool, "tool_uuid", None),
            "name": getattr(tool, "name", None),
        },
        "arguments": arguments or {},
        "initial_context": dict(call_context_vars or {}),
        "gathered_context": dict(gathered_context_vars or {}),
    }
    timeout_seconds = float(resolver.get("timeout_ms", 3000)) / 1000.0
    logger.debug(
        "Transfer resolver request prepared "
        f"resolution_id={resolution_id} "
        f"argument_keys={list((arguments or {}).keys())} "
        f"arguments={_safe_log_dict(arguments)} "
        f"initial_context_keys={list((call_context_vars or {}).keys())} "
        f"gathered_context_keys={list((gathered_context_vars or {}).keys())}"
    )

    try:
        started_at = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
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


def _fallback_resolution(
    *,
    config: dict[str, Any],
    resolver: dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
    resolution_id: Optional[str] = None,
) -> Optional[ResolvedTransferConfig]:
    fallback_route = config.get("fallback_route")
    if fallback_route:
        return _expand_approved_route(
            route_key=str(fallback_route),
            config=config,
            call_context_vars=call_context_vars,
            gathered_context_vars=gathered_context_vars,
            resolution_id=resolution_id,
            source="fallback_route",
        )

    if resolver.get("policy") == "approved_routes_or_static_fallback":
        return _resolve_static_transfer(
            config,
            call_context_vars,
            gathered_context_vars,
            resolution_id=resolution_id,
            source="static_fallback",
        )

    return None


def _resolve_from_response(
    *,
    response_data: dict[str, Any],
    config: dict[str, Any],
    resolver: dict[str, Any],
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
    resolution_id: str,
) -> ResolvedTransferConfig:
    route_key = response_data.get("route")
    if route_key:
        resolved = _expand_approved_route(
            route_key=str(route_key),
            config=config,
            call_context_vars=call_context_vars,
            gathered_context_vars=gathered_context_vars,
            resolution_id=resolution_id,
            source="http_resolver_route",
        )
        resolved.metadata.update(dict(response_data.get("metadata") or {}))
        if response_data.get("message"):
            resolved.message = str(response_data["message"])
        if response_data.get("timeout_seconds") is not None:
            try:
                resolved.timeout_seconds = min(
                    max(int(response_data["timeout_seconds"]), 5), 120
                )
            except (TypeError, ValueError) as exc:
                raise TransferResolutionError(
                    "invalid_timeout", "Transfer resolver returned invalid timeout"
                ) from exc
        return resolved

    policy = resolver.get("policy", "approved_routes_only")
    if policy != "allow_raw_destination":
        logger.warning(
            "Transfer resolver rejected response "
            f"resolution_id={resolution_id} reason=route_required "
            f"policy={policy} response_keys={list(response_data.keys())}"
        )
        raise TransferResolutionError(
            "route_required",
            "Transfer resolver must return an approved route for this policy",
        )

    destination = _normalize_destination_value(response_data.get("destination"))
    try:
        timeout = int(response_data.get("timeout_seconds", _base_timeout(config)))
    except (TypeError, ValueError) as exc:
        raise TransferResolutionError(
            "invalid_timeout", "Transfer resolver returned invalid timeout"
        ) from exc
    return ResolvedTransferConfig(
        destination=destination,
        timeout_seconds=min(max(timeout, 5), 120),
        message=response_data.get("message"),
        source="http_resolver_raw_destination",
        resolution_id=resolution_id,
        metadata=dict(response_data.get("metadata") or {}),
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

    resolver = config.get("resolver")
    if not isinstance(resolver, dict) or resolver.get("type") != "http":
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
    approved_routes = config.get("approved_routes") or {}
    logger.info(
        "Transfer resolver started "
        f"resolution_id={resolution_id} tool_uuid={getattr(tool, 'tool_uuid', None)} "
        f"workflow_run_id={workflow_run_id} type={resolver.get('type')} "
        f"policy={resolver.get('policy', 'approved_routes_only')} "
        f"timeout_ms={resolver.get('timeout_ms', 3000)} "
        f"route_count={len(approved_routes) if isinstance(approved_routes, dict) else 0} "
        f"fallback_route={config.get('fallback_route') or ''} "
        f"static_fallback_available={bool(config.get('destination'))}"
    )

    try:
        response_data = await _execute_http_resolver(
            resolver=resolver,
            tool=tool,
            arguments=arguments,
            call_context_vars=call_context_vars,
            gathered_context_vars=gathered_context_vars,
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            resolution_id=resolution_id,
        )
        resolved = _resolve_from_response(
            response_data=response_data,
            config=config,
            resolver=resolver,
            call_context_vars=call_context_vars,
            gathered_context_vars=gathered_context_vars,
            resolution_id=resolution_id,
        )
    except TransferResolutionError as exc:
        fallback = _fallback_resolution(
            config=config,
            resolver=resolver,
            call_context_vars=call_context_vars,
            gathered_context_vars=gathered_context_vars,
            resolution_id=resolution_id,
        )
        if fallback:
            logger.warning(
                "Transfer resolver failed; using configured fallback "
                f"resolution_id={resolution_id} reason={exc.reason} "
                f"fallback_source={fallback.source} route={fallback.route or ''} "
                f"destination={_mask_destination(fallback.destination)}"
            )
            return fallback
        logger.warning(
            "Transfer resolver failed without fallback "
            f"resolution_id={resolution_id} reason={exc.reason}"
        )
        raise

    if not resolved.destination:
        raise TransferResolutionError(
            "no_destination", "Transfer resolver did not provide a destination"
        )
    logger.info(
        "Transfer destination resolved "
        f"resolution_id={resolution_id} source={resolved.source} "
        f"route={resolved.route or ''} destination={_mask_destination(resolved.destination)} "
        f"timeout={resolved.timeout_seconds}"
    )
    return resolved
