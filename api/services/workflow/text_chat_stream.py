"""Delivery of a single accepted text-chat turn while it executes.

The execution belongs to the accepted message, not to the HTTP connection.
Disconnecting only detaches the listener; the turn still saves its final state.
A client recovers with the authenticated session GET, never by replaying a POST.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from fastapi.responses import StreamingResponse
from loguru import logger

from api.db.models import WorkflowRunTextSessionModel
from api.services.workflow.text_chat_session_service import (
    _mark_pending_turn_failed,
    execute_pending_text_chat_turn,
)

# Strong references keep accepted executions alive after their listener disconnects.
# This is task ownership, not shared session state; recovery reads the database.
_running_turns: dict[asyncio.Task[None], "TextChatTurnStream"] = {}

# The Helm shutdown reserve also covers event delivery and resource teardown.
SHUTDOWN_TURN_GRACE_SECONDS = 10
SHUTDOWN_SAVE_SECONDS = 5
SHUTDOWN_CANCEL_SECONDS = 5


class TextChatEventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


@dataclass
class TextChatStreamUpdate:
    kind: Literal["event", "complete", "error", "ping"]
    event: dict[str, Any] | None = None
    session: WorkflowRunTextSessionModel | None = None
    error: Exception | None = None


class TextChatTurnStream:
    def __init__(
        self,
        *,
        workflow_id: int,
        run_id: int,
        text_session: WorkflowRunTextSessionModel,
    ) -> None:
        self._queue: asyncio.Queue[TextChatStreamUpdate] = asyncio.Queue()
        self._listening = True
        self._run_id = run_id
        self._text_session = text_session
        self._events: list[dict[str, Any]] = []
        self.task = asyncio.create_task(
            self._execute(workflow_id, run_id, text_session),
            name=f"text-chat-turn-{run_id}",
        )
        _running_turns[self.task] = self
        self.task.add_done_callback(lambda task: _running_turns.pop(task, None))

    def _on_event(self, event: dict[str, Any]) -> None:
        self._events.append(event)
        self._publish(TextChatStreamUpdate(kind="event", event=event))

    def _publish(self, update: TextChatStreamUpdate) -> None:
        if self._listening:
            self._queue.put_nowait(update)

    async def _execute(
        self, workflow_id: int, run_id: int, text_session: WorkflowRunTextSessionModel
    ) -> None:
        try:
            completed = await execute_pending_text_chat_turn(
                workflow_id=workflow_id,
                run_id=run_id,
                text_session=text_session,
                on_event=self._on_event,
            )
        except Exception as error:  # noqa: BLE001 - failures must terminate the already-open SSE response
            logger.exception("Text chat turn failed for run {}", run_id)
            self._publish(TextChatStreamUpdate(kind="error", error=error))
        else:
            self._publish(TextChatStreamUpdate(kind="complete", session=completed))

    def disconnect(self) -> None:
        self._listening = False
        while not self._queue.empty():
            self._queue.get_nowait()

    async def interrupt_for_shutdown(self) -> None:
        error = RuntimeError("Chat turn interrupted by server shutdown")
        try:
            # Persist before cancellation: pipeline/tool cleanup can itself take
            # too long. The revision guard prevents overwriting a finished turn
            # and prevents late execution from overwriting this failure.
            async with asyncio.timeout(SHUTDOWN_SAVE_SECONDS):
                await _mark_pending_turn_failed(
                    run_id=self._run_id,
                    text_session=self._text_session,
                    error_message=str(error),
                    events=self._events,
                )
        except Exception:
            logger.exception(
                "Failed to persist shutdown for text chat run {}", self._run_id
            )
        finally:
            self.task.cancel()
            self._publish(TextChatStreamUpdate(kind="error", error=error))

    async def __aiter__(self) -> AsyncIterator[TextChatStreamUpdate]:
        try:
            while True:
                try:
                    update = await asyncio.wait_for(self._queue.get(), timeout=15)
                except TimeoutError:
                    yield TextChatStreamUpdate(kind="ping")
                    continue
                yield update
                if update.kind in {"complete", "error"}:
                    return
        finally:
            self.disconnect()


async def finish_streaming_text_chat_turns() -> None:
    """Finish or fail accepted turns within the application's shutdown budget."""
    turns = dict(_running_turns)
    if not turns:
        return
    _, pending = await asyncio.wait(turns, timeout=SHUTDOWN_TURN_GRACE_SECONDS)
    if not pending:
        return
    await asyncio.gather(*(turns[task].interrupt_for_shutdown() for task in pending))
    _, pending = await asyncio.wait(pending, timeout=SHUTDOWN_CANCEL_SECONDS)
    if pending:
        logger.warning(
            "{} text chat tasks still cleaning up after shutdown", len(pending)
        )
