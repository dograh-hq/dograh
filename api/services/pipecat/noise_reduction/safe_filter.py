"""Fail-open wrapper that keeps a noise-reduction engine from hurting a call."""

import time

from loguru import logger

from pipecat.audio.filters.base_audio_filter import BaseAudioFilter
from pipecat.frames.frames import FilterControlFrame

# Telephony frames are 20 ms. An engine that keeps taking half of that on the
# event loop starves every other call on the worker, so it is switched off.
DEFAULT_FRAME_BUDGET_MS = 10.0
DEFAULT_MAX_CONSECUTIVE_SLOW_FRAMES = 50
DEFAULT_MAX_ERRORS = 5


class SafeAudioFilter(BaseAudioFilter):
    """Wrap an engine so failures pass audio through instead of breaking the call.

    The engine is bypassed for the rest of the call when it fails to start,
    raises ``max_errors`` times, or overruns ``frame_budget_ms`` on
    ``max_consecutive_slow_frames`` frames in a row. Timing stats are logged
    when the transport stops.
    """

    def __init__(
        self,
        engine: BaseAudioFilter,
        engine_name: str,
        *,
        frame_budget_ms: float = DEFAULT_FRAME_BUDGET_MS,
        max_consecutive_slow_frames: int = DEFAULT_MAX_CONSECUTIVE_SLOW_FRAMES,
        max_errors: int = DEFAULT_MAX_ERRORS,
    ) -> None:
        self._engine = engine
        self._engine_name = engine_name
        self._frame_budget_ns = int(frame_budget_ms * 1_000_000)
        self._max_consecutive_slow_frames = max_consecutive_slow_frames
        self._max_errors = max_errors

        self._active = False
        self._disabled_reason: str | None = None
        self._errors = 0
        self._consecutive_slow_frames = 0
        self._frames = 0
        self._total_ns = 0
        self._max_ns = 0

    @property
    def active(self) -> bool:
        """Whether audio is currently going through the engine."""
        return self._active

    @property
    def disabled_reason(self) -> str | None:
        """Why the engine was bypassed, or ``None`` while it is in use."""
        return self._disabled_reason

    async def start(self, sample_rate: int):
        """Start the engine, falling back to passthrough if it cannot start."""
        try:
            await self._engine.start(sample_rate)
        except Exception as e:  # noqa: BLE001 - a broken engine must not fail the call
            self._disable(f"start failed: {e}")
            return
        self._active = True
        logger.info(
            f"Noise reduction ({self._engine_name}) started at {sample_rate} Hz"
        )

    async def stop(self):
        """Stop the engine and log how it performed."""
        try:
            await self._engine.stop()
        except Exception as e:  # noqa: BLE001 - cleanup must stay best-effort
            logger.warning(f"Noise reduction ({self._engine_name}) stop failed: {e}")
        self._active = False
        if self._frames:
            avg_ms = self._total_ns / self._frames / 1_000_000
            logger.info(
                f"Noise reduction ({self._engine_name}) summary: "
                f"frames={self._frames} avg_ms={avg_ms:.3f} "
                f"max_ms={self._max_ns / 1_000_000:.3f} errors={self._errors} "
                f"disabled_reason={self._disabled_reason}"
            )

    async def process_frame(self, frame: FilterControlFrame):
        """Forward control frames to the engine while it is in use."""
        if not self._active:
            return
        try:
            await self._engine.process_frame(frame)
        except Exception as e:  # noqa: BLE001 - a broken engine must not fail the call
            logger.warning(
                f"Noise reduction ({self._engine_name}) control frame failed: {e}"
            )

    async def filter(self, audio: bytes) -> bytes:
        """Return the engine's output, or ``audio`` unchanged if it is bypassed."""
        if not self._active:
            return audio

        started = time.perf_counter_ns()
        try:
            filtered = await self._engine.filter(audio)
        except Exception as e:  # noqa: BLE001 - fall back to the original audio
            self._errors += 1
            logger.warning(
                f"Noise reduction ({self._engine_name}) error "
                f"{self._errors}/{self._max_errors}: {e}"
            )
            if self._errors >= self._max_errors:
                self._disable(f"{self._errors} errors")
            return audio

        elapsed = time.perf_counter_ns() - started
        self._frames += 1
        self._total_ns += elapsed
        self._max_ns = max(self._max_ns, elapsed)
        if elapsed > self._frame_budget_ns:
            self._consecutive_slow_frames += 1
            if self._consecutive_slow_frames >= self._max_consecutive_slow_frames:
                self._disable(
                    f"{self._consecutive_slow_frames} consecutive frames over "
                    f"{self._frame_budget_ns / 1_000_000:.1f} ms"
                )
        else:
            self._consecutive_slow_frames = 0
        return filtered

    def _disable(self, reason: str):
        self._active = False
        self._disabled_reason = reason
        logger.warning(
            f"Noise reduction ({self._engine_name}) disabled for this call, "
            f"passing audio through: {reason}"
        )
