"""Voice and transcriber usage that Langfuse can put a price on.

Langfuse prices an observation as usage x the per-unit price of the model
definition its model name matches, and it reads the usage only from
``langfuse.observation.usage_details``: a JSON object whose keys must equal
the usage types of that model definition
(https://langfuse.com/docs/observability/features/token-and-cost-tracking).

Pipecat already measures what TTS and STT providers bill for, but records it
as plain span attributes: ``metrics.character_count`` on TTS spans and
``metrics.audio_seconds`` on STT spans. Langfuse shows those as metadata and
leaves the observation at "no cost". This module copies them into the usage
details as ``characters`` and ``seconds`` when the spans are exported, so a
Langfuse model definition with a ``characters`` or ``seconds`` price is all an
operator needs to see what each call's voice and transcriber cost.

Spans finish before they are exported and can no longer be changed, so the
enriched span is a copy with one more attribute; every other span passes
through as the same object.
"""

import json

from opentelemetry.sdk.trace import ReadableSpan

LANGFUSE_USAGE_DETAILS = "langfuse.observation.usage_details"

# Usage types, as typed into a Langfuse model definition's prices.
TTS_USAGE_TYPE = "characters"
STT_USAGE_TYPE = "seconds"

_TTS_CHARACTER_COUNT = "metrics.character_count"
_STT_AUDIO_SECONDS = "metrics.audio_seconds"


def usage_details_for(attributes) -> dict | None:
    """The Langfuse usage a TTS or STT span's attributes describe, if any."""
    if not attributes or LANGFUSE_USAGE_DETAILS in attributes:
        return None
    operation = attributes.get("gen_ai.operation.name")
    if operation == "tts":
        characters = attributes.get(_TTS_CHARACTER_COUNT)
        if isinstance(characters, (int, float)) and characters > 0:
            return {TTS_USAGE_TYPE: int(characters)}
    elif operation == "stt":
        seconds = attributes.get(_STT_AUDIO_SECONDS)
        if isinstance(seconds, (int, float)) and seconds > 0:
            return {STT_USAGE_TYPE: round(float(seconds), 3)}
    return None


def with_langfuse_usage(span: ReadableSpan) -> ReadableSpan:
    """Return ``span`` with Langfuse usage details added when it has billable usage."""
    usage = usage_details_for(span.attributes)
    if usage is None:
        return span
    attributes = dict(span.attributes)
    attributes[LANGFUSE_USAGE_DETAILS] = json.dumps(usage)
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=span.events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )
