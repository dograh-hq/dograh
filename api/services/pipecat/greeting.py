"""Temporarily use a two-word start strategy while a greeting is playing."""

import asyncio
from collections.abc import Awaitable, Callable

from api.services.pipecat.speech_playback import (
    PlaybackOutcome,
    SpeechPlayback,
    SpeechPlaybackTracker,
)
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    StartFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMUserAggregator,
    UserTurnStoppedMessage,
)
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies


class GreetingController:
    """Install greeting strategies and commit successfully played greeting text."""

    def __init__(
        self, playback: SpeechPlaybackTracker, get_context: Callable[[], LLMContext]
    ):
        self._playback = playback
        self._get_context = get_context
        self._user: LLMUserAggregator | None = None
        self._normal_strategies: UserTurnStrategies | None = None
        self._turn_text: asyncio.Future[str] | None = None
        self._speech_start: Frame | None = None
        self._playback.on_greeting_finished = self._finished
        self.log_generated_speech: Callable[[str], Awaitable[None]] | None = None

    def bind(self, user: LLMUserAggregator) -> None:
        self._user = user
        user.add_event_handler("on_before_process_frame", self._update_strategies)
        user.add_event_handler("on_user_turn_stopped", self._turn_stopped)

    async def _update_strategies(self, user: LLMUserAggregator, frame: Frame) -> None:
        if isinstance(frame, StartFrame):
            return
        controller = user.user_turn_controller
        if self._playback.greeting_pending and self._normal_strategies is None:
            self._normal_strategies = controller.user_turn_strategies
            greeting_strategy = MinWordsUserTurnStartStrategy(min_words=2)
            self._turn_text = asyncio.get_running_loop().create_future()
            await controller.update_strategies(
                UserTurnStrategies(
                    start=[greeting_strategy], stop=self._normal_strategies.stop
                )
            )
            # Protect the queued greeting too, and account for a bot-start
            # notification that may have arrived before strategy installation.
            await greeting_strategy.process_frame(BotStartedSpeakingFrame())
        elif (
            not self._playback.greeting_pending and self._normal_strategies is not None
        ):
            normal = self._normal_strategies
            self._normal_strategies = None
            await controller.update_strategies(normal)
            # A caller may still be speaking when playback finishes, without
            # having crossed the greeting threshold. Let the restored strategy
            # adopt that onset so it can finish the turn on the final transcript.
            if self._speech_start and not controller.has_active_user_turn:
                for strategy in normal.start:
                    await strategy.process_frame(self._speech_start)
        if isinstance(
            frame, (ProposedUserStartedSpeakingFrame, VADUserStartedSpeakingFrame)
        ):
            self._speech_start = frame
        elif isinstance(
            frame, (ProposedUserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)
        ):
            self._speech_start = None

    @property
    def awaiting_turn(self) -> bool:
        return (
            self._playback.greeting is not None
            and self._playback.greeting.outcome is PlaybackOutcome.INTERRUPTED
            and self._turn_text is not None
            and not self._turn_text.done()
        )

    async def _turn_stopped(
        self, _user, _strategy, message: UserTurnStoppedMessage
    ) -> None:
        if self._turn_text is not None and not self._turn_text.done():
            self._turn_text.set_result(message.content or "")

    async def wait_for_turn(self) -> bool:
        if self._turn_text is None:
            return False
        return bool(await asyncio.shield(self._turn_text))

    def _finished(self, speech: SpeechPlayback) -> None:
        text = self._playback.greeting_text(speech)
        if text and speech.outcome is PlaybackOutcome.PLAYED:
            self._get_context().add_message({"role": "assistant", "content": text})
        # Configured greetings use persist_to_logs on their speech request.
        # Generated greetings only know their spoken text after playback.
        if speech.text is None and text and self.log_generated_speech and self._user:
            self._user.create_task(self.log_generated_speech(text))
        if self._user and speech.outcome is not PlaybackOutcome.CLOSED:
            # Serialize restoration with incoming caller frames, including when
            # the call is silent after playback.
            self._user.create_task(self._user.queue_frame(Frame()))
