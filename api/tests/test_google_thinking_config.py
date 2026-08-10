"""Regression test: QA-grader thinking config must match the Google model family.

``thinking_budget`` is only valid for Gemini 2.5; Gemini 3 uses ``thinking_level``,
and the two must not be mixed. Unknown/legacy/custom models must get no thinking
config at all, so an unsupported setting can't make the grader reject the request
(the raised ``max_tokens`` ceiling alone still guards against truncation).
"""

from api.services.pipecat.service_factory import _google_thinking_for_model


def test_gemini_25_uses_thinking_budget():
    tc = _google_thinking_for_model("gemini-2.5-flash")
    assert tc is not None
    assert tc.thinking_budget == 4096
    assert getattr(tc, "thinking_level", None) is None


def test_gemini_3_uses_thinking_level():
    tc = _google_thinking_for_model("gemini-3-pro-preview")
    assert tc is not None
    assert tc.thinking_level == "low"
    assert tc.thinking_budget is None


def test_unknown_or_legacy_model_omits_thinking():
    assert _google_thinking_for_model("gemini-1.5-pro") is None
    assert _google_thinking_for_model("some-custom-model") is None
    assert _google_thinking_for_model("") is None
