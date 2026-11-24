"""TTS and STT usage that Langfuse can price.

Covers the usage read off TTS and STT spans, the copy the exporter sends in
place of a finished span, the routing exporter sending it to every project,
and Murf, which left Langfuse without a model name to price.
"""

import json
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pipecat.utils.tracing.service_decorators import _get_model_name

from api.services.pipecat import tracing_config
from api.services.pipecat.langfuse_usage import (
    LANGFUSE_USAGE_DETAILS,
    usage_details_for,
    with_langfuse_usage,
)
from api.services.pipecat.murf_tts import MurfTTSService


def _finished_span(name, attributes):
    """A real span, ended, as an exporter receives it."""
    captured = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(captured))
    with provider.get_tracer("pipecat").start_as_current_span(name) as span:
        for key, value in attributes.items():
            span.set_attribute(key, value)
    (finished,) = captured.get_finished_spans()
    return finished


def _tts(characters=107, **extra):
    return {
        "gen_ai.operation.name": "tts",
        "gen_ai.request.model": "falcon-2",
        "metrics.character_count": characters,
        **extra,
    }


def _stt(seconds=4.2567, **extra):
    return {
        "gen_ai.operation.name": "stt",
        "gen_ai.request.model": "scribe_v2_realtime",
        "metrics.audio_seconds": seconds,
        **extra,
    }


# ── Usage read off the attributes ─────────────────────────────────────


def test_tts_characters_become_usage():
    assert usage_details_for(_tts(107)) == {"characters": 107}


def test_stt_seconds_become_usage_to_the_millisecond():
    assert usage_details_for(_stt(4.2567)) == {"seconds": 4.257}


@pytest.mark.parametrize(
    "attributes",
    [
        None,
        {},
        _tts(0),
        _stt(0.0),
        {"gen_ai.operation.name": "tts"},
        {"gen_ai.operation.name": "stt"},
        _tts(characters="107"),
        # LLM spans already carry token usage of their own.
        {"gen_ai.operation.name": "chat", "metrics.character_count": 50},
        # Usage already set upstream is left as it is.
        _tts(**{LANGFUSE_USAGE_DETAILS: '{"characters": 1}'}),
    ],
)
def test_nothing_to_bill_adds_no_usage(attributes):
    assert usage_details_for(attributes) is None


# ── The exported copy ─────────────────────────────────────────────────


def test_a_tts_span_is_exported_with_usage_and_otherwise_unchanged():
    span = _finished_span("tts", _tts(107, **{"dograh.org_id": "7"}))

    exported = with_langfuse_usage(span)

    assert json.loads(exported.attributes[LANGFUSE_USAGE_DETAILS]) == {
        "characters": 107
    }
    for key, value in span.attributes.items():
        assert exported.attributes[key] == value
    assert exported.name == span.name
    assert exported.context == span.context
    assert exported.parent == span.parent
    assert (exported.start_time, exported.end_time) == (span.start_time, span.end_time)
    assert exported.resource == span.resource
    assert exported.instrumentation_scope == span.instrumentation_scope
    assert exported.status.status_code == span.status.status_code
    # The finished span itself is never touched.
    assert LANGFUSE_USAGE_DETAILS not in span.attributes


def test_an_stt_span_is_exported_with_seconds():
    exported = with_langfuse_usage(_finished_span("stt", _stt(2.5)))
    assert json.loads(exported.attributes[LANGFUSE_USAGE_DETAILS]) == {"seconds": 2.5}


def test_a_span_with_nothing_to_bill_passes_through_as_is():
    span = _finished_span("llm", {"gen_ai.operation.name": "chat"})
    assert with_langfuse_usage(span) is span


# ── The routing exporter ──────────────────────────────────────────────


def _capturing_exporter():
    exporter = MagicMock()
    exporter.export.return_value = SpanExportResult.SUCCESS
    return exporter


def _sent(exporter):
    return [span for call in exporter.export.call_args_list for span in call.args[0]]


def test_the_default_project_gets_usage_on_tts_and_stt_spans_only():
    default = _capturing_exporter()
    routing = tracing_config._OrgRoutingExporter(default_exporter=default)
    llm = _finished_span("llm", {"gen_ai.operation.name": "chat"})

    result = routing.export(
        [_finished_span("tts", _tts(40)), _finished_span("stt", _stt(3)), llm]
    )

    assert result == SpanExportResult.SUCCESS
    tts, stt, sent_llm = _sent(default)
    assert json.loads(tts.attributes[LANGFUSE_USAGE_DETAILS]) == {"characters": 40}
    assert json.loads(stt.attributes[LANGFUSE_USAGE_DETAILS]) == {"seconds": 3.0}
    assert sent_llm is llm


def test_an_orgs_own_project_gets_the_usage_too():
    default = _capturing_exporter()
    org_exporter = _capturing_exporter()
    routing = tracing_config._OrgRoutingExporter(default_exporter=default)
    routing._org_exporters["7"] = org_exporter

    routing.export([_finished_span("tts", _tts(12, **{"dograh.org_id": "7"}))])

    assert _sent(default) == []
    (tts,) = _sent(org_exporter)
    assert json.loads(tts.attributes[LANGFUSE_USAGE_DETAILS]) == {"characters": 12}


# ── The model name Langfuse matches prices on ────────────────────────────


def test_murf_reports_its_model():
    service = MurfTTSService(
        api_key="k",
        params=MurfTTSService.InputParams(voice_id="Anisha", model="falcon-2"),
    )
    assert _get_model_name(service) == "falcon-2"
