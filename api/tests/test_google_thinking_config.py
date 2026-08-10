"""Regression test: QA-grader thinking config must match the Google model family.

``thinking_budget`` is only valid for Gemini 2.5; Gemini 3 uses ``thinking_level``,
and the two must not be mixed. Any id that isn't a recognized 2.5/3 family (custom,
legacy, future, or one that merely embeds the family name) must get no thinking
config, so an unsupported setting can't make the grader reject the request — the
raised ``max_tokens`` ceiling alone still guards against truncation.

The last test exercises the real wiring: the QA ``max_tokens`` plus the guarded
thinking config must build valid Google *and* Vertex settings.
"""

from api.services.pipecat.service_factory import (
    GoogleLLMSettings,
    GoogleVertexLLMSettings,
    _google_thinking_for_model,
)

QA_MAX_TOKENS = 16384


def test_gemini_25_uses_thinking_budget():
    tc = _google_thinking_for_model("gemini-2.5-flash")
    assert tc is not None
    assert tc.thinking_budget == 4096
    assert getattr(tc, "thinking_level", None) is None


def test_gemini_25_revision_still_matches():
    # dated/preview revisions still start with the family prefix
    assert _google_thinking_for_model("gemini-2.5-flash-002").thinking_budget == 4096


def test_gemini_3_uses_thinking_level():
    tc = _google_thinking_for_model("gemini-3-pro-preview")
    assert tc is not None
    assert tc.thinking_level == "low"
    assert tc.thinking_budget is None


def test_unknown_legacy_or_embedded_substring_omits_thinking():
    # legacy family, a custom id, ids that only *contain* the family name, empty
    for m in (
        "gemini-1.5-pro",
        "some-custom-model",
        "acme-gemini-2.5-wrapper",
        "x-gemini-3-y",
        "",
    ):
        assert _google_thinking_for_model(m) is None, m


def test_qa_settings_accept_max_tokens_and_family_thinking():
    """The real wiring: QA max_tokens + guarded thinking build valid settings."""
    for Settings in (GoogleLLMSettings, GoogleVertexLLMSettings):
        tc = _google_thinking_for_model("gemini-2.5-flash")
        s = Settings(
            model="gemini-2.5-flash",
            temperature=0.1,
            max_tokens=QA_MAX_TOKENS,
            thinking=tc,
        )
        assert s.max_tokens == QA_MAX_TOKENS
        assert s.thinking is not None and s.thinking.thinking_budget == 4096

        # legacy model: max_tokens still applied, no thinking config attached
        legacy = Settings(
            model="gemini-1.5-pro",
            temperature=0.1,
            max_tokens=QA_MAX_TOKENS,
            thinking=_google_thinking_for_model("gemini-1.5-pro"),
        )
        assert legacy.max_tokens == QA_MAX_TOKENS
        assert legacy.thinking is None
