"""Execute accepted tools independently of an agent's pipeline tasks."""

import asyncio
import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallHandler, FunctionCallParams

if TYPE_CHECKING:
    from api.services.workflow.agent_runtime import AgentRuntime
    from api.services.workflow.pipecat_engine import PipecatEngine

DEFAULT_TOOL_TIMEOUT_SECONDS = 10.0


@dataclass
class ToolExecution:
    task: asyncio.Task[None] | None = None
    result: Any = None
    properties: FunctionCallResultProperties | None = None
    received_result: bool = False
    delivered: bool = False


class ToolExecutionOwner:
    """Keep invocation tasks and results alive until run finalization.

    The pipeline may cancel its waiter, but only the tool's own deadline
    cancels the execution. Completed entries prevent duplicate invocations.
    """

    def __init__(self, engine: "PipecatEngine", agent: "AgentRuntime"):
        self._engine = engine
        self._agent = agent
        self._executions: dict[str, ToolExecution] = {}

    def get(self, tool_call_id: str) -> ToolExecution | None:
        return self._executions.get(tool_call_id)

    def start(
        self,
        handler: FunctionCallHandler,
        params: FunctionCallParams,
        timeout_secs: float | None,
    ) -> ToolExecution:
        existing = self.get(params.tool_call_id)
        if existing is not None:
            return existing
        execution = ToolExecution()
        self._executions[params.tool_call_id] = execution
        execution.task = asyncio.create_task(
            self._execute(
                execution, handler, params, timeout_secs or DEFAULT_TOOL_TIMEOUT_SECONDS
            ),
            name=f"tool:{self._agent.visit_id}:{params.tool_call_id}",
        )
        return execution

    async def _execute(
        self,
        execution: ToolExecution,
        handler: FunctionCallHandler,
        params: FunctionCallParams,
        timeout_secs: float,
    ) -> None:
        async def capture(
            result: Any, *, properties: FunctionCallResultProperties | None = None
        ) -> None:
            if execution.received_result:
                return
            execution.result = result
            execution.properties = properties
            execution.received_result = properties is None or properties.is_final

        owned_params = copy.copy(params)
        owned_params.result_callback = capture
        status = "completed"
        try:
            async with asyncio.timeout(timeout_secs):
                await handler(owned_params)
            if not execution.received_result:
                status = "failed"
                execution.result = {
                    "status": "error",
                    "error": "Tool returned without a result",
                }
        except TimeoutError:
            status = "timeout"
            execution.result = {"status": "error", "error": "Tool execution timed out"}
        except asyncio.CancelledError:
            status = "cancelled"
            execution.result = {
                "status": "error",
                "error": "Tool execution was cancelled",
            }
            raise
        except Exception as exc:  # noqa: BLE001 - isolate failures of arbitrary tool handlers
            status = "failed"
            execution.result = {"status": "error", "error": str(exc)}
            logger.exception(
                "Tool {} [{}] failed", params.function_name, params.tool_call_id
            )
        finally:
            record = {
                "visit_id": self._agent.visit_id,
                "tool_call_id": params.tool_call_id,
                "function_name": params.function_name,
                "arguments": dict(params.arguments),
                "status": status,
                "result": execution.result,
            }
            self._engine.record_tool_result(record)
            logger.info(
                "Tool {} [{}] finished: {} (run={}, visit={})",
                params.function_name,
                params.tool_call_id,
                status,
                self._engine._workflow_run_id,
                self._agent.visit_id,
            )

    async def finish(self) -> None:
        """Wait for accepted invocations, preserving their individual deadlines."""
        while pending := [
            e.task
            for e in self._executions.values()
            if e.task is not None and not e.task.done()
        ]:
            await asyncio.shield(asyncio.gather(*pending, return_exceptions=True))
