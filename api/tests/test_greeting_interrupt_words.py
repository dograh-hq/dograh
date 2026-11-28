"""Greeting barge-in counts words the same way as the normal turn-start rule."""

import pytest
from pipecat.frames.frames import TranscriptionFrame
from pipecat.turns.types import ProcessFrameResult

from api.services.pipecat.greeting import GreetingUserTurnStartStrategy
from api.services.pipecat.speech_playback import PlaybackOutcome, SpeechPlaybackTracker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text, interrupts",
    [
        # Chinese has no spaces between words; each character is a word.
        ("不要继续说了", True),
        ("停", False),
        # Japanese is written the same way: each kana or kanji is a word.
        ("やめてください", True),
        ("止", False),
        ("please stop", True),
        ("stop", False),
    ],
)
async def test_greeting_interrupt_counts_spoken_words(text, interrupts):
    speech = SpeechPlaybackTracker().create(greeting=True)
    speech.interrupt_min_words = 2
    strategy = GreetingUserTurnStartStrategy(speech)
    started = []

    @strategy.event_handler("on_user_turn_started")
    async def on_user_turn_started(_strategy, _params):
        started.append(True)
        speech.finish(PlaybackOutcome.INTERRUPTED)

    result = await strategy.process_frame(TranscriptionFrame(text, "user", "now"))
    speech.finish(PlaybackOutcome.CLOSED)

    assert bool(started) is interrupts
    assert result is (
        ProcessFrameResult.STOP if interrupts else ProcessFrameResult.CONTINUE
    )
