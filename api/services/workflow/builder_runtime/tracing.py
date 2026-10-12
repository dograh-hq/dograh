"""Builder tracing through Dograh's existing OpenTelemetry exporter.

One trace per turn/save, one scoped Langfuse session across turns. The context is
server-owned and never reconstructed from browser headers or model arguments.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode

from api.services.observability.trace_payloads import trace_json

_TRACER = trace.get_tracer("dograh.workflow_builder")


@dataclass(frozen=True)
class BuilderTrace:
    parent: Context
    metadata: dict[str, str]

    def attributes(self) -> dict:
        return {
            "session.id": self.metadata["builder_session_id"],
            "user.id": self.metadata["dograh_user_id"],
            "langfuse.trace.tags": ["workflow_builder"],
            "langfuse.trace.public": False,
            **{
                f"langfuse.observation.metadata.{key}": value
                for key, value in self.metadata.items()
            },
        }


current_builder_trace: ContextVar[BuilderTrace | None] = ContextVar(
    "current_builder_trace", default=None
)


def builder_trace_metadata() -> dict[str, str]:
    current = current_builder_trace.get()
    return dict(current.metadata) if current else {}


def trace_error(span, message: str) -> None:
    span.set_status(Status(StatusCode.ERROR, message))
    span.set_attribute("langfuse.observation.level", "ERROR")
    span.set_attribute("langfuse.observation.status_message", message)
    span.set_attribute("builder.outcome", "error")
    span.set_attribute(
        "langfuse.observation.output",
        trace_json({"outcome": "error", "message": message}),
    )


@contextmanager
def builder_trace(*, user, session_id, operation: str, input: dict, request_id=None):
    metadata = {
        "usage_context": "workflow_builder",
        "dograh_organization_id": str(user.selected_organization_id),
        "dograh_user_id": str(user.id),
        "builder_session_id": (
            f"workflow-builder:{user.selected_organization_id}:{user.id}:{session_id}"
        ),
    }
    if request_id is not None:
        metadata["builder_turn_id"] = str(request_id)
    with _TRACER.start_as_current_span(
        f"workflow_builder.{operation}",
        context=Context(),
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        scope = BuilderTrace(trace.set_span_in_context(span, Context()), metadata)
        span.set_attributes(scope.attributes())
        span.set_attribute("langfuse.trace.name", f"workflow_builder.{operation}")
        span.set_attribute("langfuse.observation.input", trace_json(input))
        token = current_builder_trace.set(scope)
        try:
            yield span
        except (asyncio.CancelledError, GeneratorExit):
            span.set_attribute("builder.outcome", "cancelled")
            span.set_attribute("langfuse.observation.level", "WARNING")
            span.set_attribute(
                "langfuse.observation.status_message", "Builder disconnected"
            )
            raise
        except Exception as exc:
            # Never attach raw upstream bodies, credentials, or exception chains.
            trace_error(span, f"Builder {operation} failed ({type(exc).__name__})")
            raise
        finally:
            current_builder_trace.reset(token)


def record_turn_event(span, event: dict) -> None:
    if event["type"] == "error":
        trace_error(span, event["message"])
    elif event["type"] == "session":
        state = event["session"]
        pending = state.get("pending", [])
        questions = [item for item in pending if item["kind"] == "questions"]
        approvals = [item for item in pending if item["kind"] == "plan_approval"]
        proposal = state.get("proposal")
        outcome = (
            "waiting_for_answers"
            if questions
            else (
                "waiting_for_approval"
                if approvals
                else (
                    "running"
                    if state.get("can_continue")
                    else "proposed"
                    if proposal
                    else "completed"
                )
            )
        )
        span.set_attribute("builder.outcome", outcome)
        span.set_attribute(
            "langfuse.observation.output",
            trace_json(
                {
                    "outcome": outcome,
                    "message": next(
                        (
                            m["content"]
                            for m in reversed(state["messages"])
                            if m["role"] == "assistant"
                        ),
                        None,
                    ),
                    "questions": questions,
                    "plan_approvals": approvals,
                    "proposal": (
                        {k: proposal[k] for k in ("name", "summary")}
                        if proposal
                        else None
                    ),
                    "checkpoint": state.get("checkpoint"),
                }
            ),
        )
