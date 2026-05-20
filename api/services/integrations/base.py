from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from fastapi import APIRouter

from api.services.workflow.node_data import BaseNodeData
from api.services.workflow.node_specs._base import NodeSpec


class IntegrationRuntimeSession(Protocol):
    name: str

    def attach(self, task: Any) -> None: ...

    async def on_call_finished(
        self,
        *,
        gathered_context: dict[str, Any],
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class IntegrationRuntimeContext:
    workflow_run_id: int
    workflow_run: Any
    workflow_graph: Any
    run_definition: Any
    user_config: Any
    is_realtime: bool
    context_messages_provider: Callable[[], list[dict[str, Any]]]


@dataclass(frozen=True)
class IntegrationCompletionContext:
    workflow_run_id: int
    workflow_run: Any
    workflow_definition: dict[str, Any]
    definition_id: int | None
    organization_id: int
    public_token: str | None


RuntimeFactory = Callable[
    [IntegrationRuntimeContext],
    list[IntegrationRuntimeSession],
]
CompletionHandler = Callable[
    [list[dict[str, Any]], IntegrationCompletionContext],
    Awaitable[dict[str, Any]],
]


@dataclass(frozen=True)
class IntegrationNodeRegistration:
    type_name: str
    data_model: type[BaseNodeData]
    node_spec: NodeSpec
    sensitive_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrationPackageSpec:
    name: str
    nodes: tuple[IntegrationNodeRegistration, ...] = ()
    routers: tuple[APIRouter, ...] = ()
    create_runtime_sessions: RuntimeFactory | None = None
    run_completion: CompletionHandler | None = None
