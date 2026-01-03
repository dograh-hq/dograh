from __future__ import annotations

"""Callback factory helpers for :pyclass:`~api.services.workflow.pipecat_engine.PipecatEngine`.

Each helper takes a :class:`PipecatEngine` instance and returns an async
callback function suitable for passing to the various pipeline processors.
Separating these helpers into their own module keeps
``pipecat_engine.py`` focused on high-level engine orchestration logic while
encapsulating the callback implementations here for easier maintenance and
unit-testing.
"""

import re
from typing import TYPE_CHECKING, Awaitable, Callable

from loguru import logger

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSSpeakFrame,
)
from pipecat.processors.filters.stt_mute_filter import STTMuteFilter
from pipecat.utils.enums import EndTaskReason

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine
    from pipecat.processors.user_idle_processor import UserIdleProcessor


# ---------------------------------------------------------------------------
# STT mute handling
# ---------------------------------------------------------------------------


def create_should_mute_callback(
    engine: "PipecatEngine",
) -> Callable[[STTMuteFilter], Awaitable[bool]]:
    """Return a callback indicating whether STT should be muted.

    STT is muted when *interruptions are **not*** allowed on the current node.
    """

    async def callback(_: STTMuteFilter) -> bool:  # noqa: D401
        if engine._current_node is None:
            # Default to not muting if we have no active node yet.
            return False

        logger.debug(
            f"STT mute callback: allow_interrupt={engine._current_node.allow_interrupt}"
        )
        return not engine._current_node.allow_interrupt

    return callback


# ---------------------------------------------------------------------------
# User-idle handling
# ---------------------------------------------------------------------------


def create_user_idle_callback(engine: "PipecatEngine"):
    """Return a callback that handles user-idle timeouts."""

    async def handle_user_idle(
        user_idle: "UserIdleProcessor", retry_count: int
    ) -> bool:
        logger.debug(f"Handling user_idle, attempt: {retry_count}")

        if retry_count == 1:
            # Simulate an LLM generation, so that we can have the LLM context
            # updated with the new message
            await engine.task.queue_frames(
                [
                    LLMFullResponseStartFrame(),
                    TTSSpeakFrame("Just checking in to see if you're still there."),
                    LLMFullResponseEndFrame(),
                ]
            )
            return True

        # Second attempt: terminate the call due to inactivity.
        await user_idle.push_frame(
            TTSSpeakFrame("It seems like you're busy right now. Have a nice day!")
        )
        await engine.send_end_task_frame(
            EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value
        )
        return False

    return handle_user_idle


# ---------------------------------------------------------------------------
# Max-duration handling
# ---------------------------------------------------------------------------


def create_max_duration_callback(engine: "PipecatEngine"):
    """Return a callback that ends the task when the max call duration is exceeded."""

    async def handle_max_duration():
        logger.debug("Max call duration exceeded. Terminating call")
        await engine.send_end_task_frame(EndTaskReason.CALL_DURATION_EXCEEDED.value)

    return handle_max_duration


# ---------------------------------------------------------------------------
# Generation-started handling
# ---------------------------------------------------------------------------


def create_generation_started_callback(engine: "PipecatEngine"):
    """Return a callback that resets flags at the start of each LLM generation."""

    async def handle_generation_started():
        logger.debug("LLM generation started in callback processor")
        # Clear reference text from previous generation
        engine._current_llm_generation_reference_text = ""

    return handle_generation_started


def create_aggregation_correction_callback(engine: "PipecatEngine"):
    """Create a callback that uses engine's reference text to correct corrupted aggregation."""

    def correct_corrupted_aggregation(ref: str, corrupted: str) -> str:
        """Correct corrupted text by aligning it with reference text.

        This is a pure function that doesn't depend on engine instance.
        """
        # 1) Safety check: if ref (minus spaces) is shorter than corrupted, bail out
        # also if corrupted is less than 10 characters, lets also return that since most likely
        # Elevenlabs returned the right alignment
        alnum_corr = "".join(ch for ch in corrupted if ch.isalnum())
        alnum_ref = "".join(ch for ch in ref if ch.isalnum())

        if corrupted in ref or len(alnum_ref) < len(alnum_corr) or len(alnum_corr) < 10:
            return corrupted

        # 2) Find where in `ref` we should start aligning.
        #    We take the first N (N=10) characters of `corrupted`
        #    and look for all their occurrences in `ref`.
        #    We pick the *last* one
        prefix = corrupted[:10]

        # find all start‐indices of that prefix in ref
        starts = [m.start() for m in re.finditer(re.escape(prefix), ref)]
        start_idx = starts[-1] if starts else 0

        # 3) Now run the same two‑pointer scan from start_idx
        i, j = start_idx, 0
        out_chars = []
        while i < len(ref) and j < len(corrupted):
            r_ch, c_ch = ref[i], corrupted[j]
            if r_ch == c_ch:
                out_chars.append(r_ch)
                i += 1
                j += 1

            elif c_ch == " ":
                # extra space in corrupted → skip it
                j += 1

            elif r_ch == " " or r_ch in ".,;:!?":
                # missing structural char in corrupted → emit from ref
                out_chars.append(r_ch)
                i += 1

            else:
                # letter mismatch → best‑effort copy from ref
                out_chars.append(r_ch)
                i += 1
                j += 1

        # 4) A final check - the final created output should be exactly
        # as corrupted sentence sans whitespace.
        alnum_out = "".join([ch for ch in out_chars if ch.isalnum()])
        if alnum_out != alnum_corr:
            return corrupted

        # 5) Join and return exactly what we built
        return "".join(out_chars)

    def correct_aggregation(corrupted: str) -> str:
        reference = engine._current_llm_generation_reference_text

        if not reference:
            logger.warning("No reference text available for aggregation correction")
            return corrupted

        # Apply the correction algorithm
        corrected = correct_corrupted_aggregation(reference, corrupted)
        return corrected

    return correct_aggregation
