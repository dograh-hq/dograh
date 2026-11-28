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
import math

from loguru import logger
from opentelemetry.sdk.trace import ReadableSpan

LANGFUSE_USAGE_DETAILS = "langfuse.observation.usage_details"

# Usage types, as typed into a Langfuse model definition's prices.
TTS_USAGE_TYPE = "characters"
STT_USAGE_TYPE = "seconds"

_TTS_CHARACTER_COUNT = "metrics.character_count"
_STT_AUDIO_SECONDS = "metrics.audio_seconds"


def _billable(value) -> float | None:
    """``value`` as a positive, finite number, or None.

    Span attributes can hold any type, and a stray ``inf`` would make ``int()``
    raise or ``json.dumps`` write ``Infinity``, which is not valid JSON.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def usage_details_for(attributes) -> dict | None:
    """The Langfuse usage a TTS or STT span's attributes describe, if any."""
    if not attributes or LANGFUSE_USAGE_DETAILS in attributes:
        return None
    operation = attributes.get("gen_ai.operation.name")
    if operation == "tts":
        characters = _billable(attributes.get(_TTS_CHARACTER_COUNT))
        if characters is not None:
            return {TTS_USAGE_TYPE: int(characters)}
    elif operation == "stt":
        seconds = _billable(attributes.get(_STT_AUDIO_SECONDS))
        if seconds is not None:
            return {STT_USAGE_TYPE: round(seconds, 3)}
    return None


def with_langfuse_usage(span: ReadableSpan) -> ReadableSpan:
    """Return ``span`` with Langfuse usage details added when it has billable usage.

    Runs inside the exporter, where an exception would drop the whole batch,
    so any failure here exports the span unchanged instead.
    """
    try:
        usage = usage_details_for(span.attributes)
        if usage is None:
            return span
        attributes = dict(span.attributes)
        attributes[LANGFUSE_USAGE_DETAILS] = json.dumps(usage, allow_nan=False)
        return _copy_with_attributes(span, attributes)
    except Exception as e:
        logger.warning(f"Could not add Langfuse usage to span {span.name!r}: {e}")
        return span


def _copy_with_attributes(span: ReadableSpan, attributes: dict) -> ReadableSpan:
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
