import pytest
from pipecat.utils.enums import RealtimeFeedbackType

from api.services.observability.call_events.metrics import ttfb_kind
from api.services.workflow.qa.metrics import compute_call_metrics


@pytest.mark.parametrize(
    "processor,expected",
    [
        ("VendorLLMService#19", "llm"),
        ("VendorTTSService#11", "tts"),
        # Every ...STTService contains "TTS" (S-TTS-ERVICE).
        ("VendorSTTService#3", "stt"),
        ("vendorsttservice", "stt"),
        # Speech-to-speech: the model is the generation latency.
        ("VendorRealtimeLLMService#2", "llm"),
        ("BaseOutputTransport#0", None),
        ("", None),
        (None, None),
    ],
)
def test_ttfb_kind(processor, expected):
    assert ttfb_kind(processor) == expected


def _ttfb(seconds, kind=None):
    payload = {"ttfb_seconds": seconds, "processor": "X", "model": None}
    if kind is not None:
        payload["kind"] = kind
    return {"type": RealtimeFeedbackType.TTFB_METRIC.value, "payload": payload}


def test_avg_ttfb_counts_only_llm_measurements():
    logs = [_ttfb(1.0, "llm"), _ttfb(3.0, "llm"), _ttfb(0.1, "stt"), _ttfb(0.2, "tts")]
    assert compute_call_metrics(logs)["avg_ttfb_seconds"] == 2.0


def test_avg_ttfb_reads_untagged_events_as_llm():
    """Runs recorded before STT/TTS were forwarded carry no kind."""
    assert compute_call_metrics([_ttfb(1.0), _ttfb(3.0)])["avg_ttfb_seconds"] == 2.0


def test_avg_ttfb_is_none_without_llm_measurements():
    logs = [_ttfb(0.1, "stt"), _ttfb(0.2, "tts")]
    assert compute_call_metrics(logs)["avg_ttfb_seconds"] is None
