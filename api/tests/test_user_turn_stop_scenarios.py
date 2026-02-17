"""Tests validating user turn stop strategy behavior during bot speaking scenarios.

These tests validate the scenarios described in scenarios.md. They demonstrate
how the ExternalUserTurnStopStrategy and UserTurnController interact when frames
are suppressed (muted) during bot speaking.

Key concepts:
- When the bot is speaking, AlwaysUserMuteStrategy causes the LLMUserAggregator
  to suppress user frames (UserStartedSpeaking, UserStoppedSpeaking, Transcription, VAD).
- The ExternalUserTurnStopStrategy accumulates _text from TranscriptionFrames and
  triggers a stop when _user_speaking is False and _text is truthy.
- The UserTurnController only allows a stop if _user_turn is True (a start must
  have occurred first). When a stop is rejected, the controller unconditionally
  resets all stop strategies, clearing any dangling state (e.g. _text).
- This unconditional reset prevents stale _text from causing premature stops
  or contaminating subsequent turns.
"""

import asyncio

import pytest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndTaskFrame,
    Frame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests import MockLLMService
from pipecat.turns.user_mute import AlwaysUserMuteStrategy
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.time import time_now_iso8601

# Short timeout for faster tests
STOP_STRATEGY_TIMEOUT = 0.15
# Delay to allow async processing
ASYNC_DELAY = 0.05
# Delay to wait for stop strategy timeout to fire
TIMEOUT_WAIT = STOP_STRATEGY_TIMEOUT + 0.1


class FrameInjector(FrameProcessor):
    """Simple processor that can inject frames into the pipeline."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

    async def inject(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ):
        """Inject a frame into the pipeline."""
        await self.push_frame(frame, direction)


def _build_components(llm_steps=None):
    """Build pipeline components for testing.

    Uses:
    - VADUserTurnStartStrategy: turn starts only when VADUserStartedSpeakingFrame arrives
    - ExternalUserTurnStopStrategy: turn stops based on UserStoppedSpeakingFrame + _text
    - AlwaysUserMuteStrategy: suppresses user frames while bot is speaking

    Returns a tuple of (injector, user_aggregator, stop_strategy, turn_controller, mock_llm, pipeline).
    """
    context = LLMContext()

    stop_strategy = ExternalUserTurnStopStrategy(timeout=STOP_STRATEGY_TIMEOUT)

    user_turn_strategies = UserTurnStrategies(
        start=[VADUserTurnStartStrategy()],
        stop=[stop_strategy],
    )

    user_params = LLMUserAggregatorParams(
        user_turn_strategies=user_turn_strategies,
        user_mute_strategies=[AlwaysUserMuteStrategy()],
    )
    assistant_params = LLMAssistantAggregatorParams(expect_stripped_words=True)

    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params, user_params=user_params
    )
    user_agg = context_aggregator.user()
    assistant_agg = context_aggregator.assistant()

    if llm_steps is None:
        llm_steps = [
            MockLLMService.create_text_chunks(text="Response 1"),
            MockLLMService.create_text_chunks(text="Response 2"),
            MockLLMService.create_text_chunks(text="Response 3"),
        ]
    mock_llm = MockLLMService(mock_steps=llm_steps, chunk_delay=0.001)

    injector = FrameInjector()
    pipeline = Pipeline([injector, user_agg, mock_llm, assistant_agg])

    turn_controller = user_agg._user_turn_controller

    return (
        injector,
        user_agg,
        stop_strategy,
        turn_controller,
        mock_llm,
        context,
        pipeline,
    )


async def _run_scenario(pipeline, inject_fn):
    """Run a pipeline with a frame injection coroutine."""
    task = PipelineTask(pipeline, params=PipelineParams(), enable_rtvi=False)
    runner = PipelineRunner()

    async def run():
        await runner.run(task)

    async def inject():
        # Wait for pipeline to start (StartFrame to propagate)
        await asyncio.sleep(ASYNC_DELAY)
        await inject_fn()

    await asyncio.gather(run(), inject())


async def _inject_user_turn(injector, text, delay=ASYNC_DELAY):
    """Inject a complete user turn: VAD start + external start + transcription + external stop.

    This simulates what happens in a real pipeline when the user speaks:
    1. VAD detects speech -> VADUserStartedSpeakingFrame (triggers turn start)
    2. External processor sends UserStartedSpeakingFrame (stop strategy tracks _user_speaking)
    3. STT produces TranscriptionFrame (stop strategy accumulates _text)
    4. External processor sends UserStoppedSpeakingFrame (stop strategy triggers stop)
    """
    await injector.inject(VADUserStartedSpeakingFrame())
    await asyncio.sleep(0)
    await injector.inject(UserStartedSpeakingFrame())
    await asyncio.sleep(0)
    await injector.inject(UserStoppedSpeakingFrame())
    await asyncio.sleep(delay)
    await injector.inject(TranscriptionFrame(text, "user-1", time_now_iso8601()))


class TestUserTurnStopScenarios:
    """Test scenarios from scenarios.md.

    Each test simulates a specific frame ordering to validate the interaction
    between ExternalUserTurnStopStrategy and UserTurnController, particularly
    around frame suppression during bot speaking.
    """

    # =========================================================================
    # Scenario 1 (✅): All frames suppressed during bot speaking
    #
    # BotStartedSpeaking (muted)
    # UserStartedSpeaking (suppressed)
    # TranscriptionFrame (suppressed)
    # UserStoppedSpeaking (suppressed)
    # BotStoppedSpeaking (unmuted)
    #
    # Stop strategy _text is empty because TranscriptionFrame was suppressed.
    # =========================================================================

    @pytest.mark.asyncio
    async def test_scenario_1_all_suppressed_then_bot_stops(self):
        """All user frames suppressed during bot speaking, then bot stops.

        Expected: _text is empty, no turn triggered, clean state.
        Second turn works correctly.
        """
        injector, user_agg, stop_strategy, turn_ctrl, mock_llm, context, pipeline = (
            _build_components()
        )

        async def inject():
            # === Turn 1: Bot speaking, all user frames suppressed ===
            await injector.inject(BotStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # These are all suppressed by mute
            await injector.inject(VADUserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(UserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(
                TranscriptionFrame("hello", "user-1", time_now_iso8601())
            )
            await asyncio.sleep(0)
            await injector.inject(UserStoppedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(VADUserStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            await injector.inject(BotStoppedSpeakingFrame())
            await asyncio.sleep(TIMEOUT_WAIT)

            # Assert: _text should be empty (all frames suppressed)
            assert stop_strategy._text == "", (
                f"Expected empty _text after all frames suppressed, got '{stop_strategy._text}'"
            )
            assert not turn_ctrl._user_turn, "Expected _user_turn to be False"

            # === Turn 2: Normal turn should work correctly ===
            await _inject_user_turn(injector, "second turn text")
            await asyncio.sleep(TIMEOUT_WAIT)

            # Assert: turn completed, _text cleared by reset
            assert stop_strategy._text == "", (
                f"Expected empty _text after clean turn, got '{stop_strategy._text}'"
            )
            assert not turn_ctrl._user_turn, (
                "Expected _user_turn to be False after turn"
            )
            assert mock_llm.get_current_step() == 1, (
                f"Expected 1 LLM call (turn 2 only), got {mock_llm.get_current_step()}"
            )

            await injector.inject(EndTaskFrame(), direction=FrameDirection.UPSTREAM)

        await _run_scenario(pipeline, inject)

    # =========================================================================
    # Scenario 2 (✅): User frames suppressed, user stops after bot stops
    #
    # BotStartedSpeaking (muted)
    # UserStartedSpeaking (suppressed)
    # TranscriptionFrame (suppressed)
    # BotStoppedSpeaking (unmuted)
    # UserStoppedSpeaking (stop strategy has no _text -> no trigger)
    # =========================================================================

    @pytest.mark.asyncio
    async def test_scenario_2_user_stops_after_bot_stops_no_text(self):
        """User stops speaking after bot stops, but transcription was suppressed.

        Expected: _text is empty because transcription was suppressed.
        UserStoppedSpeaking doesn't trigger stop (no _text).
        """
        injector, user_agg, stop_strategy, turn_ctrl, mock_llm, context, pipeline = (
            _build_components()
        )

        async def inject():
            # === Turn 1: Bot speaking, user frames partially suppressed ===
            await injector.inject(BotStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Suppressed during bot speaking
            await injector.inject(VADUserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(UserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(
                TranscriptionFrame("hello", "user-1", time_now_iso8601())
            )
            await asyncio.sleep(ASYNC_DELAY)

            # Bot stops -> unmuted
            await injector.inject(BotStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # UserStoppedSpeaking arrives after unmute, but _text is empty
            await injector.inject(UserStoppedSpeakingFrame())
            await asyncio.sleep(TIMEOUT_WAIT)

            # Assert: _text empty (TranscriptionFrame was suppressed)
            assert stop_strategy._text == "", (
                f"Expected empty _text, got '{stop_strategy._text}'"
            )
            assert not turn_ctrl._user_turn, "Expected _user_turn to be False"

            # === Turn 2: Normal turn should work ===
            await _inject_user_turn(injector, "second turn")
            await asyncio.sleep(TIMEOUT_WAIT)

            assert stop_strategy._text == "", "Expected clean _text after turn 2"
            assert mock_llm.get_current_step() == 1, (
                f"Expected 1 LLM call, got {mock_llm.get_current_step()}"
            )

            await injector.inject(EndTaskFrame(), direction=FrameDirection.UPSTREAM)

        await _run_scenario(pipeline, inject)

    # =========================================================================
    # Scenario 3 (✅ after fix): Transcription arrives after unmute
    #
    # BotStartedSpeaking (muted)
    # UserStartedSpeaking (suppressed)
    # BotStoppedSpeaking (unmuted)
    # TranscriptionFrame -> stop strategy _text = "hello"
    # UserStoppedSpeaking -> stop strategy triggers (text truthy, not speaking)
    #   Turn controller ignores (user_turn is False), BUT unconditionally
    #   resets stop strategies -> _text cleared. No dangling state.
    # =========================================================================

    @pytest.mark.asyncio
    async def test_scenario_3_transcription_after_unmute_text_cleared(self):
        """Transcription arrives after bot stops but turn was never started.

        The VADUserStartedSpeakingFrame was suppressed, so no turn started.
        But TranscriptionFrame arrives after unmute and accumulates _text.
        The stop strategy triggers, but the turn controller rejects it
        (no active turn). The unconditional reset clears _text, preventing
        any dangling state from contaminating subsequent turns.
        """
        injector, user_agg, stop_strategy, turn_ctrl, mock_llm, context, pipeline = (
            _build_components()
        )

        async def inject():
            # === Turn 1: Rejected stop with unconditional reset ===
            await injector.inject(BotStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Suppressed: VAD and UserStartedSpeaking
            await injector.inject(VADUserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(UserStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Bot stops -> unmuted
            await injector.inject(BotStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # TranscriptionFrame arrives AFTER unmute -> reaches stop strategy
            await injector.inject(
                TranscriptionFrame("hello", "user-1", time_now_iso8601())
            )
            await asyncio.sleep(ASYNC_DELAY)

            # Install spy on trigger_user_turn_stopped to track every call
            # and the _user_turn state at the time of each call.
            trigger_stop_calls = []
            original_trigger_stop = stop_strategy.trigger_user_turn_stopped

            async def spy_trigger_stop():
                trigger_stop_calls.append(turn_ctrl._user_turn)
                await original_trigger_stop()

            stop_strategy.trigger_user_turn_stopped = spy_trigger_stop

            # UserStoppedSpeaking arrives AFTER unmute
            # Stop strategy: _user_speaking is False (UserStartedSpeaking was suppressed),
            # _text is "hello" -> triggers stop via _handle_user_stopped_speaking
            # Turn controller: _user_turn is False -> rejects, but resets -> _text cleared
            await injector.inject(UserStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Call #1: _handle_user_stopped_speaking -> _maybe_trigger_user_turn_stopped
            assert len(trigger_stop_calls) == 1, (
                f"Expected exactly 1 trigger_user_turn_stopped call from "
                f"_handle_user_stopped_speaking, got {len(trigger_stop_calls)}"
            )
            assert trigger_stop_calls[0] is False, (
                "Expected _user_turn=False when _handle_user_stopped_speaking triggered stop"
            )

            # Wait for _task_handler timeout period
            await asyncio.sleep(TIMEOUT_WAIT)

            # The unconditional reset cleared _text after the rejected stop,
            # so the timeout's _maybe_trigger_user_turn_stopped sees _text="" and
            # does NOT call trigger_user_turn_stopped again.
            assert len(trigger_stop_calls) == 1, (
                f"Expected no additional trigger_user_turn_stopped calls after "
                f"reset cleared _text, but got {len(trigger_stop_calls)} total call(s)"
            )

            # Restore original method
            stop_strategy.trigger_user_turn_stopped = original_trigger_stop

            # Transcript is not suppressed, so we should have hello in user aggregator
            assert user_agg._aggregation[0].text == "hello"

            # Assert: _text is cleared by the unconditional reset (no dangling state)
            assert stop_strategy._text == "", (
                f"Expected empty _text after unconditional reset, got '{stop_strategy._text}'"
            )
            assert not turn_ctrl._user_turn, (
                "Expected _user_turn to be False (turn was never started)"
            )
            # No LLM call should have happened
            assert mock_llm.get_current_step() == 0, (
                f"Expected 0 LLM calls, got {mock_llm.get_current_step()}"
            )

            # === Turn 2: No premature stop, normal flow ===
            # _text is clean, so UserStoppedSpeaking won't trigger a premature stop.
            # The turn completes normally when the timeout fires after TranscriptionFrame.
            # The aggregator still has dangling "hello" from turn 1, which gets
            # combined with turn 2's "world" — this is acceptable behavior.
            await _inject_user_turn(injector, "world")
            await asyncio.sleep(TIMEOUT_WAIT)

            assert stop_strategy._text == "", (
                f"Expected clean _text after normal turn, got '{stop_strategy._text}'"
            )
            assert mock_llm.get_current_step() == 1, (
                f"Expected 1 LLM call (normal turn), got {mock_llm.get_current_step()}"
            )

            # The LLM received both "hello" (dangling in aggregator from turn 1)
            # and "world" (from turn 2). This is acceptable — the aggregator's
            # _aggregation is a separate concern from the stop strategy's _text.
            messages = context.messages
            user_messages = [m for m in messages if m.get("role") == "user"]
            assert len(user_messages) == 1, (
                f"Expected 1 user message, got {len(user_messages)}"
            )
            user_text = user_messages[0]["content"]
            assert "hello" in user_text, (
                f"Expected 'hello' (from aggregator) in user message, got: '{user_text}'"
            )
            assert "world" in user_text, (
                f"Expected 'world' (from turn 2) in user message, got: '{user_text}'"
            )

            await injector.inject(EndTaskFrame(), direction=FrameDirection.UPSTREAM)

        await _run_scenario(pipeline, inject)

    # =========================================================================
    # Scenario 4 (✅): User speaks after bot stops -> normal flow
    #
    # BotStartedSpeaking (muted)
    # BotStoppedSpeaking (unmuted)
    # UserStartedSpeaking (triggers interruption/turn start)
    # TranscriptionFrame
    # UserStoppedSpeaking
    #
    # Turn starts because VAD frame is not suppressed. Everything works.
    # =========================================================================

    @pytest.mark.asyncio
    async def test_scenario_4_user_speaks_after_bot_stops(self):
        """User speaks after bot stops speaking. Normal flow, everything works.

        All frames arrive after unmute, so VAD triggers turn start normally.
        """
        injector, user_agg, stop_strategy, turn_ctrl, mock_llm, context, pipeline = (
            _build_components()
        )

        async def inject():
            # === Turn 1: Bot speaks, then user speaks after bot stops ===
            await injector.inject(BotStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)
            await injector.inject(BotStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Normal user turn after bot stopped
            await _inject_user_turn(injector, "hello after bot")
            await asyncio.sleep(TIMEOUT_WAIT)

            # Assert: clean state
            assert stop_strategy._text == "", (
                f"Expected empty _text after clean turn, got '{stop_strategy._text}'"
            )
            assert not turn_ctrl._user_turn, "Expected _user_turn False after turn"
            assert mock_llm.get_current_step() == 1, (
                f"Expected 1 LLM call, got {mock_llm.get_current_step()}"
            )

            # === Turn 2: Another normal turn ===
            await _inject_user_turn(injector, "second turn")
            await asyncio.sleep(TIMEOUT_WAIT)

            assert stop_strategy._text == "", "Expected clean _text after turn 2"
            assert mock_llm.get_current_step() == 2, (
                f"Expected 2 LLM calls, got {mock_llm.get_current_step()}"
            )

            # Verify clean context - each turn should be separate
            user_messages = [m for m in context.messages if m.get("role") == "user"]
            assert len(user_messages) == 2, (
                f"Expected 2 user messages (one per turn), got {len(user_messages)}"
            )

            await injector.inject(EndTaskFrame(), direction=FrameDirection.UPSTREAM)

        await _run_scenario(pipeline, inject)

    # =========================================================================
    # Scenario 5 (✅): Late transcription - all suppressed
    #
    # BotStartedSpeaking (muted)
    # UserStartedSpeaking (suppressed)
    # UserStoppedSpeaking (suppressed)
    # TranscriptionFrame (suppressed) <- late, but still during bot speaking
    # BotStoppedSpeaking (unmuted)
    #
    # Everything suppressed, _text empty. Clean state.
    # =========================================================================

    @pytest.mark.asyncio
    async def test_scenario_5_late_transcription_all_suppressed(self):
        """Late transcription arrives during bot speaking. All suppressed.

        Even though transcription is late, it still arrives before BotStoppedSpeaking
        so it's still muted. Clean state.
        """
        injector, user_agg, stop_strategy, turn_ctrl, mock_llm, context, pipeline = (
            _build_components()
        )

        async def inject():
            # === Turn 1: Late transcription, but all still suppressed ===
            await injector.inject(BotStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            await injector.inject(VADUserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(UserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(UserStoppedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(VADUserStoppedSpeakingFrame())
            await asyncio.sleep(0)
            # Late transcription - but still during bot speaking
            await injector.inject(
                TranscriptionFrame("late hello", "user-1", time_now_iso8601())
            )
            await asyncio.sleep(ASYNC_DELAY)

            await injector.inject(BotStoppedSpeakingFrame())
            await asyncio.sleep(TIMEOUT_WAIT)

            # Assert: all suppressed, clean state
            assert stop_strategy._text == "", (
                f"Expected empty _text, got '{stop_strategy._text}'"
            )
            assert not turn_ctrl._user_turn

            # === Turn 2: Normal turn works ===
            await _inject_user_turn(injector, "clean turn")
            await asyncio.sleep(TIMEOUT_WAIT)

            assert stop_strategy._text == ""
            assert mock_llm.get_current_step() == 1

            await injector.inject(EndTaskFrame(), direction=FrameDirection.UPSTREAM)

        await _run_scenario(pipeline, inject)

    # =========================================================================
    # Scenario 6 (✅ after fix): Late transcription arrives after bot stops
    #
    # BotStartedSpeaking (muted)
    # UserStartedSpeaking (suppressed)
    # UserStoppedSpeaking (suppressed)
    # BotStoppedSpeaking (unmuted)
    # TranscriptionFrame -> reaches stop strategy, _text = "late hello"
    #
    # Stop strategy timeout fires: _user_speaking is False (from initial state,
    # UserStartedSpeaking was suppressed), _text truthy -> triggers stop.
    # Turn controller: _user_turn False -> rejects, but unconditionally resets
    # -> _text cleared. No dangling state.
    # =========================================================================

    @pytest.mark.asyncio
    async def test_scenario_6_late_transcription_after_unmute_text_cleared(self):
        """Late transcription arrives after bot stops. No turn was started.

        UserStartedSpeaking was suppressed so _user_turn never started.
        The late TranscriptionFrame accumulates _text after unmute.
        The stop strategy timeout triggers, but controller rejects it.
        The unconditional reset clears _text, preventing dangling state.
        """
        injector, user_agg, stop_strategy, turn_ctrl, mock_llm, context, pipeline = (
            _build_components()
        )

        async def inject():
            # === Turn 1: Late transcription scenario ===
            await injector.inject(BotStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Suppressed
            await injector.inject(VADUserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(UserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(UserStoppedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(VADUserStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Bot stops -> unmuted
            await injector.inject(BotStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Install spy on trigger_user_turn_stopped to track calls
            trigger_stop_calls = []
            original_trigger_stop = stop_strategy.trigger_user_turn_stopped

            async def spy_trigger_stop():
                trigger_stop_calls.append(turn_ctrl._user_turn)
                await original_trigger_stop()

            stop_strategy.trigger_user_turn_stopped = spy_trigger_stop

            # Late transcription arrives after unmute
            await injector.inject(
                TranscriptionFrame("late hello", "user-1", time_now_iso8601())
            )

            # No UserStoppedSpeakingFrame in this scenario — the stop is
            # triggered ONLY by the _task_handler timeout path.
            await asyncio.sleep(TIMEOUT_WAIT)

            # The _task_handler timeout fired _maybe_trigger_user_turn_stopped:
            # _user_speaking=False (UserStartedSpeaking was suppressed),
            # _text="late hello" -> trigger_user_turn_stopped called
            # Turn controller: _user_turn=False -> rejects, but resets -> _text cleared
            assert len(trigger_stop_calls) == 1, (
                f"Expected exactly 1 trigger_user_turn_stopped call from "
                f"_task_handler timeout, got {len(trigger_stop_calls)}"
            )
            assert trigger_stop_calls[0] is False, (
                "Expected _user_turn=False when timeout triggered stop"
            )

            # Restore original method
            stop_strategy.trigger_user_turn_stopped = original_trigger_stop

            # Transcript is not suppressed, so we should have late hello in user aggregator
            assert user_agg._aggregation[0].text == "late hello"

            # Assert: _text is cleared by the unconditional reset (no dangling state)
            assert stop_strategy._text == "", (
                f"Expected empty _text after unconditional reset, got '{stop_strategy._text}'"
            )
            assert not turn_ctrl._user_turn, "Turn should not have started"
            assert mock_llm.get_current_step() == 0, "No LLM call expected"

            # === Turn 2: No premature stop, normal flow ===
            # _text is clean, so no premature stop occurs.
            # The turn completes normally when the timeout fires after TranscriptionFrame.
            # The aggregator still has dangling "late hello" from turn 1, which gets
            # combined with turn 2's "real speech" — this is acceptable behavior.
            await _inject_user_turn(injector, "real speech")
            await asyncio.sleep(TIMEOUT_WAIT)

            assert stop_strategy._text == "", (
                f"Expected clean _text after normal turn, got '{stop_strategy._text}'"
            )
            assert mock_llm.get_current_step() == 1, (
                f"Expected 1 LLM call (normal turn), got {mock_llm.get_current_step()}"
            )

            # The LLM received both "late hello" (dangling in aggregator from turn 1)
            # and "real speech" (from turn 2).
            user_messages = [m for m in context.messages if m.get("role") == "user"]
            assert len(user_messages) == 1, (
                f"Expected 1 user message, got {len(user_messages)}"
            )
            user_text = user_messages[0]["content"]
            assert "late hello" in user_text, (
                f"Expected 'late hello' (from aggregator) in user message, got: '{user_text}'"
            )
            assert "real speech" in user_text, (
                f"Expected 'real speech' (from turn 2) in user message, got: '{user_text}'"
            )

            await injector.inject(EndTaskFrame(), direction=FrameDirection.UPSTREAM)

        await _run_scenario(pipeline, inject)

    # =========================================================================
    # Scenario 7 (✅ after fix): Late transcription - user stops before transcription
    #
    # BotStartedSpeaking (muted)
    # UserStartedSpeaking (suppressed)
    # BotStoppedSpeaking (unmuted)
    # UserStoppedSpeaking (no _text yet -> no trigger from _handle_user_stopped)
    # TranscriptionFrame -> _text = "late", timeout triggers stop
    #
    # Turn controller: _user_turn False -> rejects, but unconditionally resets
    # -> _text cleared. No dangling state.
    # =========================================================================

    @pytest.mark.asyncio
    async def test_scenario_7_late_transcription_after_user_stops_text_cleared(self):
        """User stops speaking, then late transcription arrives. No turn started.

        UserStoppedSpeaking arrives first (no _text yet, so no trigger).
        Then TranscriptionFrame arrives (sets _text). The timeout fires and
        triggers stop, but controller rejects it. The unconditional reset
        clears _text, preventing dangling state.
        """
        injector, user_agg, stop_strategy, turn_ctrl, mock_llm, context, pipeline = (
            _build_components()
        )

        async def inject():
            # === Turn 1: Late transcription after user stops ===
            await injector.inject(BotStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Suppressed
            await injector.inject(VADUserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(UserStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Bot stops -> unmuted
            await injector.inject(BotStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # UserStoppedSpeaking arrives after unmute, but _text is still empty
            # -> _maybe_trigger_user_turn_stopped: _text is "" -> no trigger
            await injector.inject(UserStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Late transcription arrives AFTER user stopped
            await injector.inject(
                TranscriptionFrame("late text", "user-1", time_now_iso8601())
            )
            # Wait for timeout to fire
            await asyncio.sleep(TIMEOUT_WAIT)

            # Transcript is not suppressed, so we should have late text in user aggregator
            assert user_agg._aggregation[0].text == "late text"

            # Assert: _text is cleared by the unconditional reset
            # The timeout fired _maybe_trigger_user_turn_stopped:
            # _user_speaking=False (was never set, UserStartedSpeaking suppressed),
            # _text="late text" -> triggers stop
            # Turn controller: _user_turn=False -> rejects, but resets -> _text cleared
            assert stop_strategy._text == "", (
                f"Expected empty _text after unconditional reset, got '{stop_strategy._text}'"
            )
            assert not turn_ctrl._user_turn
            assert mock_llm.get_current_step() == 0

            # === Turn 2: No premature stop, normal flow ===
            # _text is clean, so no premature stop occurs.
            # The turn completes normally when the timeout fires after TranscriptionFrame.
            # The aggregator still has dangling "late text" from turn 1, which gets
            # combined with turn 2's "next speech" — this is acceptable behavior.
            await _inject_user_turn(injector, "next speech")
            await asyncio.sleep(TIMEOUT_WAIT)

            assert stop_strategy._text == "", (
                f"Expected clean _text after normal turn, got '{stop_strategy._text}'"
            )
            assert mock_llm.get_current_step() == 1

            # The LLM received both "late text" (dangling in aggregator from turn 1)
            # and "next speech" (from turn 2).
            user_messages = [m for m in context.messages if m.get("role") == "user"]
            assert len(user_messages) == 1
            user_text = user_messages[0]["content"]
            assert "late text" in user_text, (
                f"Expected 'late text' (from aggregator) in context, got: '{user_text}'"
            )
            assert "next speech" in user_text, (
                f"Expected 'next speech' (from turn 2) in context, got: '{user_text}'"
            )

            await injector.inject(EndTaskFrame(), direction=FrameDirection.UPSTREAM)

        await _run_scenario(pipeline, inject)

    # =========================================================================
    # Scenario 8 (✅): Late transcription - user speaks after bot stops
    #
    # BotStartedSpeaking (muted)
    # BotStoppedSpeaking (unmuted)
    # UserStartedSpeaking (not suppressed -> turn starts, start strategies reset)
    # UserStoppedSpeaking (no _text -> no trigger)
    # TranscriptionFrame (timeout triggers stop)
    #
    # Turn controller: _user_turn IS True -> allows stop -> resets strategies
    # Clean state!
    # =========================================================================

    @pytest.mark.asyncio
    async def test_scenario_8_late_transcription_user_speaks_after_bot_stops(self):
        """User speaks after bot stops, then late transcription arrives.

        Because user spoke after unmute, VAD triggers turn start -> _user_turn=True.
        When the late transcription triggers the stop, controller allows it and
        resets strategies. Clean state.
        """
        injector, user_agg, stop_strategy, turn_ctrl, mock_llm, context, pipeline = (
            _build_components()
        )

        async def inject():
            # === Turn 1: Late transcription but user spoke after unmute ===
            await injector.inject(BotStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)
            await injector.inject(BotStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # User speaks AFTER bot stops -> not suppressed
            await injector.inject(VADUserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(UserStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # User stops speaking (no _text yet, so stop strategy doesn't trigger)
            await injector.inject(UserStoppedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject(VADUserStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Late transcription arrives
            await injector.inject(
                TranscriptionFrame("late but ok", "user-1", time_now_iso8601())
            )
            # Wait for timeout to trigger stop
            await asyncio.sleep(TIMEOUT_WAIT)

            # Assert: turn controller allowed the stop, strategies were reset
            assert stop_strategy._text == "", (
                f"Expected clean _text after allowed stop, got '{stop_strategy._text}'"
            )
            assert not turn_ctrl._user_turn, "Turn should have stopped"
            assert mock_llm.get_current_step() == 1, (
                f"Expected 1 LLM call, got {mock_llm.get_current_step()}"
            )

            # === Turn 2: Clean subsequent turn ===
            await _inject_user_turn(injector, "clean turn")
            await asyncio.sleep(TIMEOUT_WAIT)

            assert stop_strategy._text == ""
            assert mock_llm.get_current_step() == 2

            # Verify each turn is separate in context
            user_messages = [m for m in context.messages if m.get("role") == "user"]
            assert len(user_messages) == 2, (
                f"Expected 2 separate user messages, got {len(user_messages)}"
            )

            await injector.inject(EndTaskFrame(), direction=FrameDirection.UPSTREAM)

        await _run_scenario(pipeline, inject)

    # =========================================================================
    # Combined test: validates _text is cleared independently after each
    # rejected stop, preventing accumulation across muted periods.
    # =========================================================================

    @pytest.mark.asyncio
    async def test_text_cleared_independently_across_failed_stops(self):
        """Validates _text does not accumulate across multiple failed stop attempts.

        Two consecutive muted periods with late transcriptions each trigger
        a rejected stop. The unconditional reset clears _text after each
        rejection, so no accumulation occurs. The subsequent normal turn
        completes correctly.
        """
        injector, user_agg, stop_strategy, turn_ctrl, mock_llm, context, pipeline = (
            _build_components()
        )

        async def inject():
            # === Muted period 1: _text cleared after rejected stop ===
            await injector.inject(BotStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)
            await injector.inject(VADUserStartedSpeakingFrame())  # suppressed
            await asyncio.sleep(0)
            await injector.inject(UserStartedSpeakingFrame())  # suppressed
            await asyncio.sleep(ASYNC_DELAY)
            await injector.inject(BotStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            # Late transcription after unmute
            await injector.inject(
                TranscriptionFrame("first", "user-1", time_now_iso8601())
            )
            await asyncio.sleep(0)
            await injector.inject(UserStoppedSpeakingFrame())
            await asyncio.sleep(TIMEOUT_WAIT)

            # Transcript is not suppressed, so we should have first in user aggregator
            assert user_agg._aggregation[0].text == "first"

            # _text is cleared by the unconditional reset after rejected stop
            assert stop_strategy._text == "", (
                f"Expected empty _text after unconditional reset, got '{stop_strategy._text}'"
            )

            # === Muted period 2: _text cleared independently, no accumulation ===
            await injector.inject(BotStartedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)
            await injector.inject(VADUserStartedSpeakingFrame())  # suppressed
            await asyncio.sleep(0)
            await injector.inject(UserStartedSpeakingFrame())  # suppressed
            await asyncio.sleep(ASYNC_DELAY)
            await injector.inject(BotStoppedSpeakingFrame())
            await asyncio.sleep(ASYNC_DELAY)

            await injector.inject(
                TranscriptionFrame("second", "user-1", time_now_iso8601())
            )
            await asyncio.sleep(0)
            await injector.inject(UserStoppedSpeakingFrame())
            await asyncio.sleep(TIMEOUT_WAIT)

            # _text is cleared again — no accumulation of "first" + "second"
            assert stop_strategy._text == "", (
                f"Expected empty _text after second unconditional reset, got '{stop_strategy._text}'"
            )
            # Aggregator accumulated both (separate concern, acceptable)
            assert len(user_agg._aggregation) == 2
            assert user_agg._aggregation[0].text == "first"
            assert user_agg._aggregation[1].text == "second"

            # === Turn 3: No premature stop, normal flow ===
            # _text is clean, so no premature stop occurs.
            # The turn completes normally when the timeout fires after TranscriptionFrame.
            # The aggregator has dangling "first" + "second" from muted periods,
            # which get combined with turn 3's "actual speech".
            await _inject_user_turn(injector, "actual speech")
            await asyncio.sleep(TIMEOUT_WAIT)

            assert stop_strategy._text == "", (
                f"Expected clean _text after normal turn, got '{stop_strategy._text}'"
            )
            assert mock_llm.get_current_step() == 1

            # The LLM received all three: "first" + "second" (from aggregator)
            # and "actual speech" (from turn 3).
            user_messages = [m for m in context.messages if m.get("role") == "user"]
            assert len(user_messages) == 1, (
                f"Expected 1 user message, got {len(user_messages)}"
            )
            user_text = user_messages[0]["content"]
            assert "first" in user_text, f"Expected 'first' in '{user_text}'"
            assert "second" in user_text, f"Expected 'second' in '{user_text}'"
            assert "actual speech" in user_text, (
                f"Expected 'actual speech' in '{user_text}'"
            )

            await injector.inject(EndTaskFrame(), direction=FrameDirection.UPSTREAM)

        await _run_scenario(pipeline, inject)
