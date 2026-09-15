from __future__ import annotations

import dataclasses
import inspect
from typing import Any

from loguru import logger

from api.errors.failure import ErrorSource, classify_exception, log_failure
from api.services.integrations.base import (
    IntegrationCallCapabilities,
    IntegrationCompletionContext,
    IntegrationNodeRegistration,
    IntegrationPackageSpec,
    IntegrationRuntimeContext,
)
from api.services.workflow.node_data import BaseNodeData

_PACKAGE_REGISTRY: dict[str, IntegrationPackageSpec] = {}


def register_package(spec: IntegrationPackageSpec) -> IntegrationPackageSpec:
    existing = _PACKAGE_REGISTRY.get(spec.name)
    if existing is not None and existing is not spec:
        raise ValueError(
            f"Duplicate integration package registration for {spec.name!r}"
        )
    _PACKAGE_REGISTRY[spec.name] = spec
    return spec


def _ensure_loaded() -> None:
    from api.services.integrations.loader import ensure_integrations_loaded

    ensure_integrations_loaded()


def all_packages() -> list[IntegrationPackageSpec]:
    _ensure_loaded()
    return [_PACKAGE_REGISTRY[name] for name in sorted(_PACKAGE_REGISTRY)]


def get_package(name: str) -> IntegrationPackageSpec | None:
    _ensure_loaded()
    return _PACKAGE_REGISTRY.get(name)


def get_node_registration(type_name: str) -> IntegrationNodeRegistration | None:
    _ensure_loaded()
    for package in _PACKAGE_REGISTRY.values():
        for node in package.nodes:
            if node.type_name == type_name:
                return node
    return None


def get_node_data_model(type_name: str) -> type[BaseNodeData] | None:
    registration = get_node_registration(type_name)
    return registration.data_model if registration else None


def get_node_spec(type_name: str):
    registration = get_node_registration(type_name)
    return registration.node_spec if registration else None


def get_node_secret_fields(type_name: str) -> tuple[str, ...]:
    registration = get_node_registration(type_name)
    return registration.sensitive_fields if registration else ()


def all_node_specs():
    _ensure_loaded()
    specs = []
    for package in all_packages():
        specs.extend(node.node_spec for node in package.nodes)
    return specs


def all_routers():
    _ensure_loaded()
    routers = []
    for package in all_packages():
        routers.extend(package.routers)
    return routers


def create_runtime_sessions(
    context: IntegrationRuntimeContext,
):
    _ensure_loaded()
    sessions = []
    for package in all_packages():
        if package.create_runtime_sessions is None:
            continue
        sessions.extend(package.create_runtime_sessions(context))
    return sessions


def _sanitize_hooks(
    capability: IntegrationCallCapabilities,
) -> IntegrationCallCapabilities:
    """Drop any hook that is set but not callable, keeping the rest.

    ``IntegrationCallCapabilities`` is a plain dataclass, so its ``Callable``
    annotations are documentation, not enforcement — a package can set a hook
    to any object at all.

    Only the malformed hook is discarded. A package that gets its addendum
    wrong should still get its pre-call recall, which is the more valuable
    half; dropping the whole capability would cost the caller more than the
    mistake does.
    """
    invalid = {
        field: value
        for field in ("run_pre_call", "prompt_addendum")
        if (value := getattr(capability, field, None)) is not None
        and not callable(value)
    }
    if not invalid:
        return capability

    for field, value in invalid.items():
        logger.warning(
            f"Integration {capability.name!r} set {field} to "
            f"{type(value).__name__}, which is not callable; ignoring that hook"
        )
        if inspect.iscoroutine(value):
            # `run_pre_call=fetch()` instead of `run_pre_call=fetch` — the
            # coroutine is never awaited, so close it here rather than let it
            # surface as a warning at an unrelated collection point.
            value.close()

    return dataclasses.replace(capability, **dict.fromkeys(invalid))


def create_call_capabilities(
    context: IntegrationRuntimeContext,
) -> list[IntegrationCallCapabilities]:
    _ensure_loaded()
    capabilities: list[IntegrationCallCapabilities] = []
    for package in all_packages():
        if package.create_call_capabilities is None:
            continue
        try:
            capability = package.create_call_capabilities(context)
        except Exception as exc:
            # A package failing to describe itself must not stop the call.
            log_failure(
                classify_exception(
                    exc,
                    source=ErrorSource.INTEGRATION,
                    provider=package.name,
                    error_owner="user",
                ),
                workflow_run_id=context.workflow_run_id,
                integration_package=package.name,
            )
            continue
        if capability is None:
            continue
        if not isinstance(capability, IntegrationCallCapabilities):
            # Guarding the call is not enough — a factory that *returns* the
            # wrong thing (a dict, or the coroutine of an accidentally async
            # factory) would only fail later, on attribute access, past the
            # point where the call can still degrade gracefully.
            logger.warning(
                f"Integration {package.name!r} call-capabilities factory "
                f"returned {type(capability).__name__}, expected "
                f"IntegrationCallCapabilities or None; skipping"
            )
            if inspect.iscoroutine(capability):
                # An accidentally `async def` factory: the coroutine was
                # never awaited, so close it explicitly or Python warns
                # about it later at an unrelated garbage-collection point.
                capability.close()
            continue
        capabilities.append(_sanitize_hooks(capability))
    return capabilities


def iter_completion_packages(
    workflow_definition: dict[str, Any],
):
    _ensure_loaded()
    nodes = workflow_definition.get("nodes", []) if workflow_definition else []
    for package in all_packages():
        node_types = {node.type_name for node in package.nodes}
        package_nodes = [
            node
            for node in nodes
            if isinstance(node, dict) and node.get("type") in node_types
        ]
        if package_nodes:
            yield package, package_nodes


def has_completion_handlers(workflow_definition: dict[str, Any]) -> bool:
    return any(
        package.run_completion is not None
        for package, _nodes in iter_completion_packages(workflow_definition)
    )


async def run_completion_handlers(
    *,
    context: IntegrationCompletionContext,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for package, nodes in iter_completion_packages(context.workflow_definition):
        if package.run_completion is None:
            continue
        try:
            package_result = await package.run_completion(nodes, context)
        except Exception as exc:
            log_failure(
                classify_exception(
                    exc,
                    source=ErrorSource.INTEGRATION,
                    provider=package.name,
                    error_owner="user",
                ),
                organization_id=context.organization_id,
                workflow_run_id=context.workflow_run_id,
                integration_package=package.name,
            )
            results[f"integration_{package.name}"] = {
                "error": "completion_handler_failed"
            }
            continue
        if package_result:
            results.update(package_result)
    return results
