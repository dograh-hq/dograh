"""Playback ordering for workflow tools emitted by realtime providers."""

from collections.abc import Awaitable, Callable, Sequence

from loguru import logger

from pipecat.services.llm_service import FunctionCallFromLLM, LLMService

Dispatch = Callable[[list[FunctionCallFromLLM]], Awaitable[None]]


class WorkflowToolCallDeferral:
    """Only a response's sole transition may wait for playback.

    Providers with streamed tool events collect transitions until their response
    ends, so all ordinary siblings are accepted before a transition can retire
    the visit. Ordinary calls are never retained here. Only a sole transition
    waits for playback after the response is complete.
    """

    def __init__(self, service: LLMService):
        self.service = service
        self.pending: list[FunctionCallFromLLM] = []
        self._dispatch: Dispatch | None = None
        self._seen: set[str] = set()
        self._discarded: set[str] = set()
        self._response_id: str | None = None
        self._collecting = False
        self.closed = False
        self.generation = 0

    def discard(self, reason: str, *, close: bool = False) -> None:
        self.closed |= close
        self.generation += 1
        for call in self.pending:
            self._discarded.add(call.tool_call_id)
            logger.info(
                "{}: dropping deferred transition {} [{}]: {}",
                self.service,
                call.function_name,
                call.tool_call_id,
                reason,
            )
        self.pending = []
        self._dispatch = None
        self._collecting = False

    def begin_response(self, *, collecting: bool = False) -> None:
        self.discard("new_response")
        self._seen.clear()
        self._response_id = None
        self._collecting = collecting

    def select_response(
        self, response_id: str | None, *, collecting: bool = False
    ) -> None:
        if response_id is None or response_id == self._response_id:
            self._collecting |= collecting
            return
        if self._response_id is not None:
            self.begin_response()
        self._response_id = response_id
        self._collecting = collecting

    async def complete_response(
        self, response_id: str | None, *, speaking: bool, succeeded: bool = True
    ) -> None:
        if (
            response_id is not None
            and self._response_id is not None
            and response_id != self._response_id
        ):
            return
        if not succeeded:
            self.discard("response_not_completed")
            return
        self._collecting = False
        if self.pending and self._dispatch:
            await self.submit([], speaking=speaking, dispatch=self._dispatch)

    def is_single_transition(self, calls: Sequence[FunctionCallFromLLM]) -> bool:
        return (
            len(self._seen) == 1
            and len(calls) == 1
            and self.service._function_is_node_transition(calls[0].function_name)
        )

    async def submit(
        self,
        calls: Sequence[FunctionCallFromLLM],
        *,
        speaking: bool,
        dispatch: Dispatch,
    ) -> None:
        self._seen.update(call.tool_call_id for call in calls)
        ready = self.pending + list(calls)
        self.pending = []
        self._dispatch = None
        ready = [
            call
            for call in ready
            if not self.service._function_is_node_transition(call.function_name)
            or (not self.closed and call.tool_call_id not in self._discarded)
        ]
        if self._collecting:
            self.pending = [
                call
                for call in ready
                if self.service._function_is_node_transition(call.function_name)
            ]
            self._dispatch = dispatch if self.pending else None
            ordinary = [
                call
                for call in ready
                if not self.service._function_is_node_transition(call.function_name)
            ]
            if ordinary:
                await dispatch(ordinary)
        elif speaking and self.is_single_transition(ready):
            self.pending = ready
            self._dispatch = dispatch
            logger.debug(
                "{}: deferring transition {} [{}] until playback completes",
                self.service,
                ready[0].function_name,
                ready[0].tool_call_id,
            )
        elif ready:
            await dispatch(ready)

    async def release(self) -> None:
        if self._collecting:
            return
        calls, dispatch = self.pending, self._dispatch
        self.pending, self._dispatch = [], None
        if calls and dispatch and not self.closed:
            logger.debug(
                "{}: executing deferred transition {} [{}] after playback",
                self.service,
                calls[0].function_name,
                calls[0].tool_call_id,
            )
            await dispatch(calls)
