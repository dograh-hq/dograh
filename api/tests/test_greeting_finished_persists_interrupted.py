"""Regression test for GreetingController._finished and interrupted greetings.

Issue #910: a greeting interrupted after the two-word threshold was never
committed to the LLM context, so the model re-introduced itself on resume.
"""

from types import SimpleNamespace

from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.pipecat.greeting import GreetingController
from api.services.pipecat.speech_playback import PlaybackOutcome, SpeechPlaybackTracker


def _controller() -> tuple[GreetingController, LLMContext]:
    context = LLMContext()
    controller = GreetingController(
        SpeechPlaybackTracker(),
        lambda: context,
        is_screening=lambda: False,
    )
    return controller, context


def test_interrupted_greeting_is_committed_to_context():
    controller, context = _controller()
    speech = SimpleNamespace(
        text="Hi, this is Dograh calling",
        text_parts=[],
        outcome=PlaybackOutcome.INTERRUPTED,
    )

    controller._finished(speech)

    assert context.get_messages() == [
        {"role": "assistant", "content": "Hi, this is Dograh calling"}
    ]


def test_played_greeting_is_still_committed_to_context():
    controller, context = _controller()
    speech = SimpleNamespace(
        text="Hi, this is Dograh calling", text_parts=[], outcome=PlaybackOutcome.PLAYED
    )

    controller._finished(speech)

    assert context.get_messages() == [
        {"role": "assistant", "content": "Hi, this is Dograh calling"}
    ]


def test_failed_greeting_is_not_committed_to_context():
    controller, context = _controller()
    speech = SimpleNamespace(
        text="Hi, this is Dograh calling", text_parts=[], outcome=PlaybackOutcome.FAILED
    )

    controller._finished(speech)

    assert context.get_messages() == []
