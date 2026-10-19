"""Recover spoken tool syntax through Gemini's native function-call pipeline."""

from contextlib import aclosing

from google.genai.types import (
    Candidate,
    Content,
    FinishReason,
    FunctionCall,
    GenerateContentResponse,
    Part,
)

from api.services.pipecat.spoken_tool_calls import SpokenToolCallMixin


class GeminiSpokenToolCallMixin(SpokenToolCallMixin):
    """Filter one response at a time, before Pipecat accumulates text or tools.

    Native parts retain their metadata. Recovered calls are appended only after a
    successful stream, so the provider's existing dispatcher, fallback arbitration,
    tool events and context aggregation also own recovered calls.
    """

    async def _stream_response(self, context):
        advertised = context.tools
        tool_choice = context.tool_choice
        guard = self._new_spoken_tool_guard(advertised)
        generation = self._tool_response_generation
        native_names = set()
        finish_reason = None
        text_signature = None
        clean_text = ""
        single_candidate = True

        async with aclosing(super()._stream_response(context)) as stream:
            async for chunk in stream:
                candidates = []
                # The provider consumes all candidates; their text must not be
                # combined to infer one additional action across alternatives.
                if len(chunk.candidates or []) > 1:
                    single_candidate = False
                for candidate in chunk.candidates or []:
                    if candidate.index not in (None, 0):
                        single_candidate = False
                    if candidate.finish_reason:
                        finish_reason = candidate.finish_reason
                    parts = []
                    for part in (
                        candidate.content.parts
                        if candidate.content and candidate.content.parts
                        else []
                    ):
                        if part.function_call:
                            native_names.add(part.function_call.name)
                        if part.text is not None and not part.thought:
                            if part.thought_signature:
                                text_signature = part.thought_signature
                            text = guard.feed(part.text)
                            clean_text += text
                            if text:
                                parts.append(
                                    part.model_copy(
                                        update={"text": text, "thought_signature": None}
                                    )
                                )
                        else:
                            parts.append(part)
                    content = (
                        candidate.content.model_copy(update={"parts": parts})
                        if candidate.content
                        else None
                    )
                    candidates.append(candidate.model_copy(update={"content": content}))
                yield chunk.model_copy(update={"candidates": candidates})

        tail = guard.finish()
        clean_text += tail
        parts = [Part(text=tail)] if tail else []
        # Bookmark signatures against the text actually sent to context, including
        # any final lexical token. Empty/fully suppressed text has no bookmark.
        if text_signature and clean_text.strip():
            parts.append(Part(text="", thought_signature=text_signature))

        for call in self._recover_spoken_tool_calls(
            guard,
            context=context,
            advertised=advertised,
            tool_choice=tool_choice,
            generation=generation,
            native_names=native_names,
            completed=single_candidate and finish_reason == FinishReason.STOP,
        ):
            parts.append(
                Part(
                    function_call=FunctionCall(
                        id=call.tool_call_id,
                        name=call.function_name,
                        args=call.arguments,
                    )
                )
            )
        if parts:
            yield GenerateContentResponse(
                candidates=[Candidate(content=Content(role="model", parts=parts))]
            )
