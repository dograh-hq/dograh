from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from fastapi import APIRouter

from api.services.observability.call_events.base import CallEventSinkRegistration
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
class IntegrationTool:
    """An LLM function that an integration adds to every Start Call and Agent node."""

    name: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    handler: Callable[[Any], Awaitable[None]]


ToolFactory = Callable[..., list[IntegrationTool]]
"""Called as ``factory(workflow_graph, realtime=...)`` for each Start Call and Agent node."""

ContextProvider = Callable[[str], Awaitable[str | None]]
"""Knowledge to add to the system instruction for the turn that answers ``user_text``.

Runs before every cascade or text chat inference, so it must answer in
milliseconds. Returns None when it has nothing for this turn.
"""

ContextProviderFactory = Callable[[Any], list[ContextProvider]]


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
    call_event_sink: CallEventSinkRegistration | None = None
    create_tools: ToolFactory | None = None
    create_context_providers: ContextProviderFactory | None = None
