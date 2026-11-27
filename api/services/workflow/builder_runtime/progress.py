"""Forward local runner activity to the existing durable status event stream."""

from asyncio import AbstractEventLoop, Queue
from contextvars import ContextVar

progress_queue: ContextVar[tuple[AbstractEventLoop, Queue] | None] = ContextVar(
    "builder_progress", default=None
)

TOOL_PROGRESS = {
    "write_workflow": "Writing your agent…",
    "patch_workflow": "Updating your agent…",
    "edit_workflow": "Updating your agent…",
    "read_workflow": "Reading your agent…",
    "get_tool_definition_schema": "Checking integration settings…",
    "propose_tool": "Preparing an integration for approval…",
    "propose_tool_edit": "Preparing an integration change for approval…",
    "ask_questions": "Preparing a question for you…",
    "submit_brief": "Preparing your requirements…",
}


def report_progress(message: str) -> None:
    sink = progress_queue.get()
    if sink is not None:
        loop, queue = sink
        # Synchronous LangChain tools run in an executor thread.
        loop.call_soon_threadsafe(
            queue.put_nowait, {"type": "status", "message": message}
        )
