"""Recover spoken tools before OpenAI-compatible services consume their stream."""

import json

from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCallFunction

from api.services.pipecat.spoken_tool_calls import SpokenToolCallMixin


class OpenAISpokenToolCallMixin(SpokenToolCallMixin):
    """Feed cleaned text and recovered native deltas to Pipecat's normal parser.

    Wrap the provider's get_chat_completions so its request metadata, error
    handling and any thinking filter still run. The shared parser and recovery
    policy are also used by Gemini; only the wire format differs here.
    """

    async def get_chat_completions(self, context):
        advertised = context.tools
        tool_choice = context.tool_choice
        guard = self._new_spoken_tool_guard(advertised)
        generation = self._tool_response_generation
        stream = await super().get_chat_completions(context)
        return self._guard_chat_completion_stream(
            stream, context, advertised, tool_choice, guard, generation
        )

    async def _guard_chat_completion_stream(
        self, stream, context, advertised, tool_choice, guard, generation
    ):
        native_calls = {}
        finish_reason = None
        refused = False
        last_chunk = None
        iterator = stream.__aiter__()
        try:
            async for chunk in iterator:
                last_chunk = chunk
                if not chunk.choices:
                    yield chunk
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                if delta is None:
                    yield chunk
                    continue
                refused = refused or bool(delta.refusal)
                if delta.tool_calls:
                    # Preserve native tool TTFAT while assembling their argument
                    # streams. Dispatch already happens after response completion.
                    await self.stop_ttfat_metrics()
                for call in delta.tool_calls or []:
                    previous = native_calls.get(call.index)
                    functions = [
                        item.function
                        for item in (previous, call)
                        if item and item.function
                    ]
                    native_calls[call.index] = call.model_copy(
                        update={
                            "id": call.id or (previous.id if previous else None),
                            "type": "function",
                            "function": ChoiceDeltaToolCallFunction(
                                name="".join(f.name or "" for f in functions),
                                arguments="".join(f.arguments or "" for f in functions),
                            ),
                        }
                    )
                # Pipecat's base parser expects contiguous, sequential tool
                # indices; providers can interleave parallel calls. Replay the
                # assembled native calls before appending any recovered calls.
                delta = delta.model_copy(update={"tool_calls": None})
                if delta.content is not None:
                    clean = guard.feed(delta.content)
                    delta = delta.model_copy(update={"content": clean or None})
                choice = choice.model_copy(update={"delta": delta})
                chunk = chunk.model_copy(
                    update={"choices": [choice, *chunk.choices[1:]]}
                )
                yield chunk
        finally:
            # Closing an outer async generator does not close its nested stream.
            # Close both the iterator and HTTP response on cancellation as well.
            try:
                if hasattr(iterator, "aclose"):
                    await iterator.aclose()
            finally:
                if hasattr(stream, "close"):
                    await stream.close()
                elif stream is not iterator and hasattr(stream, "aclose"):
                    await stream.aclose()

        tail = guard.finish()
        if tail and last_chunk:
            yield self._spoken_tool_chunk(last_chunk, content=tail)
        named_native_calls = [
            call for _, call in sorted(native_calls.items()) if call.function.name
        ]
        for index, call in enumerate(named_native_calls):
            yield self._spoken_tool_chunk(
                last_chunk, tool_calls=[call.model_copy(update={"index": index})]
            )
        recovered = self._recover_spoken_tool_calls(
            guard,
            context=context,
            advertised=advertised,
            tool_choice=tool_choice,
            generation=generation,
            native_names={call.function.name for call in native_calls.values()},
            completed=finish_reason in ("stop", "tool_calls") and not refused,
        )
        next_index = len(named_native_calls)
        for index, call in enumerate(recovered, next_index):
            # One call per chunk matches the upstream incremental parser.
            yield self._spoken_tool_chunk(
                last_chunk,
                tool_calls=[
                    {
                        "index": index,
                        "id": call.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": call.function_name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                ],
            )

    @staticmethod
    def _spoken_tool_chunk(source, **delta):
        # Do not copy usage, finish reasons, or other provider metadata into
        # synthetic deltas: those belong to the original chunks alone.
        return ChatCompletionChunk(
            id=source.id,
            created=source.created,
            model=source.model,
            object="chat.completion.chunk",
            choices=[{"index": 0, "delta": delta, "finish_reason": None}],
        )
