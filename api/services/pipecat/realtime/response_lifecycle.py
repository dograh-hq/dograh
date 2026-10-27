"""One playback boundary pair per OpenAI-compatible realtime response.

Pipecat emits starts both when requesting a response and for its assistant
items. Only the server's response.created/response.done pair identifies a
whole response; item events may repeat and a cancelled response can end late.
"""

from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.processors.frame_processor import FrameDirection


def response_event(method):
    """Keep late output/tool events attached to the response that produced them."""

    @wraps(method)
    async def wrapped(self, evt):
        item_id = getattr(evt, "item_id", None) or getattr(
            getattr(evt, "item", None), "id", None
        )
        call_id = getattr(evt, "call_id", None) or getattr(
            getattr(evt, "item", None), "call_id", None
        )
        response_id = (
            getattr(evt, "response_id", None)
            or self._response_items.get(item_id)
            or self._response_items.get(call_id)
            or self._playback_response_id
            or getattr(self, "_current_response_id", None)
        )
        if item_id and response_id:
            self._response_items[item_id] = response_id
        if call_id and response_id:
            self._response_items[call_id] = response_id
        if response_id in self._finished_response_ids:
            return
        token = self._response_event_id.set(response_id)
        try:
            return await method(self, evt)
        finally:
            self._response_event_id.reset(token)

    return wrapped


class RealtimeResponseLifecycleMixin:
    """Normalize response boundaries without changing provider item handling.

    Used only by the OpenAI/Azure Realtime and Grok adapters. Generation ends
    here; the playback tracker still waits for the end marker to drain through
    the transport's audio queue before releasing a protected greeting.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._playback_response_id: str | None = None
        self._finished_response_ids: set[str] = set()
        self._reported_response_ids: set[str] = set()
        self._response_items: dict[str, str] = {}
        self._response_scopes: dict[str, str] = {}
        self._pending_response_scope: str | None = None
        self._response_requested = False
        self._cancel_pending_response = False
        self._response_event_id: ContextVar[str | None] = ContextVar(
            "realtime_response_event_id", default=None
        )

    async def send_client_event(self, event):
        if (
            event.type == "response.create"
            and self._playback_response_id is None
            and self._pending_response_scope is None
        ):
            # Reserve playback ownership at the request, before network I/O.
            # A late acknowledgement must not consume an expectation registered
            # for a subsequent response after this request timed out.
            self._pending_response_scope = str(uuid4())
            self._response_requested = True
            frame = LLMFullResponseStartFrame()
            frame.metadata["dograh_response_id"] = self._pending_response_scope
            await super().push_frame(frame)
        await super().send_client_event(event)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        if direction is FrameDirection.DOWNSTREAM:
            # Request/item boundaries are not response boundaries. Lifecycle
            # methods push their normalized pair via super().
            if isinstance(frame, (LLMFullResponseStartFrame, LLMFullResponseEndFrame)):
                return
            if (
                self._response_event_id.get() in self._finished_response_ids
                and isinstance(
                    frame,
                    (TextFrame, TTSAudioRawFrame, TTSStartedFrame, TTSStoppedFrame),
                )
            ):
                return
        await super().push_frame(frame, direction)

    async def _handle_evt_response_created(self, evt):
        response_id = evt.response.id
        if (
            response_id == self._playback_response_id
            or response_id in self._finished_response_ids
        ):
            return
        requested_scope = self._pending_response_scope
        self._pending_response_scope = None
        self._response_scopes[response_id] = requested_scope or response_id
        self._response_requested = False
        # Grok has a response.created handler; our pinned OpenAI service does
        # not. Preserve Grok's own response ID bookkeeping.
        handler = getattr(super(), "_handle_evt_response_created", None)
        if handler is not None:
            await handler(evt)
        if self._cancel_pending_response:
            self._cancel_pending_response = False
            self._finished_response_ids.add(response_id)
            await self._cancel_realtime_response()
            return
        self._playback_response_id = response_id
        self._workflow_tool_deferral.begin_response(collecting=True)
        self._workflow_tool_deferral.select_response(response_id, collecting=True)
        if requested_scope is None:
            frame = LLMFullResponseStartFrame()
            frame.metadata["dograh_response_id"] = response_id
            await super().push_frame(frame)

    async def _finish_playback_response(self, response_id, *, outcome=None):
        if response_id is None or response_id in self._finished_response_ids:
            return
        self._finished_response_ids.add(response_id)
        if self._playback_response_id == response_id:
            self._playback_response_id = None
        frame = LLMFullResponseEndFrame()
        frame.metadata["dograh_response_id"] = self._response_scopes.get(
            response_id, response_id
        )
        if outcome is not None:
            frame.metadata["dograh_response_outcome"] = outcome
        await super().push_frame(frame)

    async def _handle_evt_response_done(self, evt):
        response_id = evt.response.id
        if self._is_stale_response(response_id):
            # Cancellation does not eliminate the server's final usage report.
            # Calling the upstream handler here would also clear a newer
            # response's audio/item state and emit another end boundary.
            if response_id not in self._reported_response_ids:
                self._reported_response_ids.add(response_id)
                await self._report_late_response_usage(evt)
            return
        await super()._handle_evt_response_done(evt)
        self._reported_response_ids.add(response_id)
        if self._playback_response_id == response_id:
            outcome = {
                "cancelled": "interrupted",
                "failed": "failed",
                "incomplete": "failed",
            }.get(evt.response.status)
            await self._finish_playback_response(response_id, outcome=outcome)

    def _is_stale_response(self, response_id):
        return response_id in self._finished_response_ids or (
            self._playback_response_id is not None
            and response_id != self._playback_response_id
        )

    async def _handle_interruption(self):
        await self._finish_pending_response("interrupted")
        self._cancel_pending_response |= self._response_requested
        await self._finish_playback_response(
            self._playback_response_id, outcome="interrupted"
        )
        await super()._handle_interruption()

    async def _finish_pending_response(self, outcome):
        if self._pending_response_scope and not self._cancel_pending_response:
            frame = LLMFullResponseEndFrame()
            frame.metadata["dograh_response_id"] = self._pending_response_scope
            frame.metadata["dograh_response_outcome"] = outcome
            await super().push_frame(frame)

    @response_event
    async def _handle_evt_audio_delta(self, evt):
        await super()._handle_evt_audio_delta(evt)

    @response_event
    async def _handle_evt_audio_transcript_delta(self, evt):
        await super()._handle_evt_audio_transcript_delta(evt)

    @response_event
    async def _handle_evt_text_delta(self, evt):
        await super()._handle_evt_text_delta(evt)

    @response_event
    async def _handle_evt_conversation_item_added(self, evt):
        await super()._handle_evt_conversation_item_added(evt)

    async def _report_late_response_usage(self, evt):
        usage = getattr(evt, "usage", None) or evt.response.usage
        if usage is None:
            return
        inputs = getattr(usage, "input_token_details", None)
        outputs = getattr(usage, "output_token_details", None)
        cached = getattr(inputs, "cached_tokens_details", None)
        await self.start_llm_usage_metrics(
            LLMTokenUsage(
                prompt_tokens=usage.input_tokens or 0,
                completion_tokens=usage.output_tokens or 0,
                total_tokens=usage.total_tokens or 0,
                cache_read_input_tokens=getattr(inputs, "cached_tokens", None),
                input_audio_tokens=getattr(inputs, "audio_tokens", None),
                output_audio_tokens=getattr(outputs, "audio_tokens", None),
                cache_read_input_audio_tokens=getattr(cached, "audio_tokens", None),
            )
        )

    async def _disconnect(self):
        await self._finish_pending_response("closed")
        await self._finish_playback_response(
            self._playback_response_id, outcome="closed"
        )
        await super()._disconnect()
        self._finished_response_ids.clear()
        self._reported_response_ids.clear()
        self._response_items.clear()
        self._response_scopes.clear()
        self._pending_response_scope = None
        self._response_requested = False
        self._cancel_pending_response = False
