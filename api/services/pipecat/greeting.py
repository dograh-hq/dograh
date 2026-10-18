"""Temporary turn-start policies for screening and greeting playback."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from loguru import logger

from api.services.pipecat.speech_playback import (
    PlaybackOutcome,
    SpeechPlayback,
    SpeechPlaybackTracker,
)
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    StartFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMUserAggregator,
    UserTurnStoppedMessage,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_start.base_user_turn_start_strategy import (
    BaseUserTurnStartStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies


class GreetingUserTurnStartStrategy(BaseUserTurnStartStrategy):
    """Retain short utterances without opening a turn during protected playback."""

    def __init__(self, speech: SpeechPlayback):
        super().__init__()
        self.speech = speech
        self.has_input = False
        self.started_at: str | None = None
        self.stopped_at: str | None = None

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(
            frame, (ProposedUserStartedSpeakingFrame, VADUserStartedSpeakingFrame)
        ):
            self.started_at = self.started_at or datetime.now(UTC).isoformat(
                timespec="milliseconds"
            )
        elif isinstance(
            frame, (ProposedUserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)
        ):
            self.stopped_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            self.has_input = self.has_input or bool(frame.text.strip())
            if (
                not self.speech.done
                and self.speech.allow_interrupt
                and len(frame.text.split()) >= self.speech.interrupt_min_words
            ):
                await self.trigger_user_turn_started()
                await self.speech.wait()
                return ProcessFrameResult.STOP
        return ProcessFrameResult.CONTINUE


class GreetingController:
    """Own greeting interruption policy and the shared delivered-text commit."""

    def __init__(
        self,
        playback: SpeechPlaybackTracker,
        get_context: Callable[[], LLMContext],
        *,
        is_screening: Callable[[], bool],
    ):
        self._playback = playback
        self._get_context = get_context
        self._is_screening = is_screening
        self._user: LLMUserAggregator | None = None
        self._normal_strategies: UserTurnStrategies | None = None
        self._min_words: int | None = None
        self._turn_text: asyncio.Future[str] | None = None
        self._speech_start: Frame | None = None
        self._greeting_strategy: GreetingUserTurnStartStrategy | None = None
        self._greeting_message: tuple[SpeechPlayback, dict] | None = None
        self._log_tasks: set[asyncio.Task] = set()
        self._playback.on_greeting_finished = self._finished
        self._playback.on_greeting_started = self._started
        self.log_speech: Callable[[SpeechPlayback], Awaitable[None]] | None = None
        self.log_background_speech: (
            Callable[[SpeechPlayback, str, str | None, str | None], Awaitable[None]]
            | None
        ) = None
        self.on_background_turn: Callable[[dict], Awaitable[None]] | None = None
        self.get_log_context: Callable[[], dict] = dict

    def bind(self, user: LLMUserAggregator) -> None:
        self._user = user
        user.add_event_handler("on_before_process_frame", self._update_strategies)
        user.add_event_handler("on_after_process_frame", self._after_user_frame)
        user.add_event_handler("on_user_turn_stopped", self._turn_stopped)

    async def _update_strategies(self, user: LLMUserAggregator, frame: Frame) -> None:
        if isinstance(frame, StartFrame):
            return
        controller = user.user_turn_controller
        greeting_pending = self._playback.greeting_pending
        if greeting_pending:
            speech = self._playback.greeting
            if (
                self._greeting_strategy is None
                or self._greeting_strategy.speech is not speech
            ):
                if self._normal_strategies is None:
                    self._normal_strategies = controller.user_turn_strategies
                self._greeting_strategy = GreetingUserTurnStartStrategy(speech)
                self._turn_text = asyncio.get_running_loop().create_future()
                self._min_words = None
                await controller.update_strategies(
                    UserTurnStrategies(
                        start=[self._greeting_strategy],
                        stop=self._normal_strategies.stop,
                    )
                )
            min_words = None
        elif self._is_screening():
            min_words = 1
        else:
            min_words = None
        if not greeting_pending and self._greeting_strategy is not None:
            strategy = self._greeting_strategy
            if (
                strategy.speech.outcome is PlaybackOutcome.PLAYED
                and not controller.has_active_user_turn
                and not self._speech_start
                and (strategy.has_input or strategy.started_at)
                and not user.aggregation_string()
                and not isinstance(
                    frame,
                    (ProposedUserStartedSpeakingFrame, VADUserStartedSpeakingFrame),
                )
            ):
                # Recognition can finish after playback even though the caller
                # stopped during it. Commit that final as background speech too.
                return
            self._greeting_strategy = None
            if not controller.has_active_user_turn and (
                user.aggregation_string() or (strategy.has_input and self._speech_start)
            ):
                if (
                    strategy.speech.outcome is PlaybackOutcome.PLAYED
                    and not self._speech_start
                ):
                    await self._consume_background_turn(user, strategy)
                else:
                    await strategy.trigger_user_turn_started(enable_interruptions=False)
                    if self._speech_start:
                        for stop_strategy in controller.user_turn_strategies.stop:
                            await stop_strategy.process_frame(self._speech_start)
                    else:
                        await controller.force_user_turn_stop()
        if min_words is not None and min_words != self._min_words:
            if self._normal_strategies is None:
                self._normal_strategies = controller.user_turn_strategies
            # Screening admits transcripts; its supervisor decides when to interrupt.
            strategy = MinWordsUserTurnStartStrategy(
                min_words=min_words, enable_interruptions=False
            )
            await controller.update_strategies(
                UserTurnStrategies(start=[strategy], stop=self._normal_strategies.stop)
            )
            self._min_words = min_words
        elif (
            not greeting_pending
            and min_words is None
            and self._normal_strategies is not None
        ):
            normal = self._normal_strategies
            self._normal_strategies = None
            self._min_words = None
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

    async def _after_user_frame(self, user: LLMUserAggregator, frame: Frame) -> None:
        if (
            isinstance(frame, TranscriptionFrame)
            and self._greeting_strategy is not None
            and self._greeting_strategy.speech.done
        ):
            await self._update_strategies(user, Frame())

    async def _consume_background_turn(
        self, user: LLMUserAggregator, strategy: GreetingUserTurnStartStrategy
    ) -> None:
        """Keep background speech before a completed greeting, without inference."""
        text = user.aggregation_string()
        await user.reset()
        speech = strategy.speech
        context = speech.context if speech.context is not None else self._get_context()
        message = {"role": "user", "content": text}
        greeting_message = (
            self._greeting_message[1]
            if self._greeting_message and self._greeting_message[0] is speech
            else None
        )
        index = next(
            (
                i
                for i, entry in enumerate(context.messages)
                if entry is greeting_message
            ),
            len(context.messages),
        )
        context.messages.insert(index, message)
        if self._turn_text is not None and not self._turn_text.done():
            self._turn_text.set_result("")
        if self.log_background_speech:
            self._log_tasks.add(
                asyncio.create_task(
                    self.log_background_speech(
                        speech, text, strategy.started_at, strategy.stopped_at
                    )
                )
            )
        if self.on_background_turn:
            await self.on_background_turn(message)

    @property
    def awaiting_turn(self) -> bool:
        return (
            self._playback.greeting is not None
            and self._playback.greeting.done
            and self._turn_text is not None
            and not self._turn_text.done()
            and (
                self._playback.greeting.outcome is PlaybackOutcome.INTERRUPTED
                or (
                    self._user is not None
                    and self._user.user_turn_controller.has_active_user_turn
                )
                or (
                    self._greeting_strategy is not None
                    and self._greeting_strategy.has_input
                )
            )
        )

    @property
    def has_completed_turn(self) -> bool:
        return bool(
            self._turn_text is not None
            and self._turn_text.done()
            and not self._turn_text.cancelled()
            and self._turn_text.result()
        )

    async def _turn_stopped(
        self, _user, _strategy, message: UserTurnStoppedMessage
    ) -> None:
        if (
            self._turn_text is not None
            and self._turn_text.done()
            and not self._turn_text.cancelled()
            and not self._turn_text.result()
            and message.content
        ):
            # A new turn can finish while the supervised opening still waits
            # for generation readiness. Unlike background speech, it needs a reply.
            self._turn_text = asyncio.get_running_loop().create_future()
        if self._turn_text is not None and not self._turn_text.done():
            self._turn_text.set_result(message.content or "")

    async def wait_for_turn(self) -> bool:
        if self._turn_text is None:
            return False
        return bool(await asyncio.shield(self._turn_text))

    def _finished(self, speech: SpeechPlayback) -> None:
        text = self._playback.greeting_text(speech)
        if text and speech.manage_text:
            context = (
                speech.context if speech.context is not None else self._get_context()
            )
            message = {"role": "assistant", "content": text}
            context.add_message(message)
            self._greeting_message = (speech, message)
        logger.bind(**speech.log_context).info(
            "Greeting finished: speech_id={} outcome={} first_output_at={} played_ms={}",
            speech.id,
            speech.outcome.value,
            speech.first_output_at,
            round(speech.played_seconds * 1000),
        )
        if self.log_speech and speech.manage_text:
            task = asyncio.create_task(self.log_speech(speech))
            self._log_tasks.add(task)
        if self._user and speech.outcome is not PlaybackOutcome.CLOSED:
            # Serialize restoration with incoming caller frames, including when
            # the call is silent after playback.
            self._user.create_task(self._user.queue_frame(Frame()))

    def _started(self, speech: SpeechPlayback) -> None:
        speech.log_context = {**self.get_log_context(), **speech.log_context}

    def log_playback(self, speech: SpeechPlayback) -> None:
        """Persist a queued line with its playback timing once it has played.

        For a line that is neither the greeting nor written to context, so no
        other path records when the caller heard it.
        """
        speech.log_context = {**self.get_log_context(), **speech.log_context}
        log_speech = self.log_speech
        if log_speech is None:
            return

        async def log_when_finished() -> None:
            await speech.wait()
            await log_speech(speech)

        self._log_tasks.add(asyncio.create_task(log_when_finished()))

    async def flush(self) -> None:
        tasks = tuple(self._log_tasks)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            self._log_tasks.difference_update(tasks)
            for result in results:
                if isinstance(result, BaseException):
                    logger.error(
                        "Greeting transcript persistence failed ({})",
                        type(result).__name__,
                    )
