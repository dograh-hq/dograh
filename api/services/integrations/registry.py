from __future__ import annotations

from typing import Any

from api.services.integrations.base import (
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
        package_result = await package.run_completion(nodes, context)
        if package_result:
            results.update(package_result)
    return results
