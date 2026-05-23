from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    MetricsFrame,
    StartFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.turn_tracking_observer import TurnTrackingObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.processors.frame_processor import FrameDirection
from pipecat.utils.context.message_sanitization import strip_thought_ids_from_messages
from tuner_pipecat_sdk.accumulator import CallAccumulator
from tuner_pipecat_sdk.payload_builder import build_payload

from api.enums import WorkflowRunMode

TUNER_RECORDING_PLACEHOLDER = "pipecat://no-recording"


@dataclass(frozen=True)
class _PayloadConfig:
    call_id: str
    call_type: str
    recording_url: str
    asr_model: str
    llm_model: str
    tts_model: str
    sip_call_id: str | None = None
    sip_headers: dict[str, str] | None = None
    agent_version: int | None = None


def mode_to_tuner_call_type(mode: str | None) -> str:
    if mode in {
        WorkflowRunMode.WEBRTC.value,
        WorkflowRunMode.SMALLWEBRTC.value,
    }:
        return "web_call"
    return "phone_call"


class TunerCollector(BaseObserver):
    """Collect runtime call metadata and build a deferred Tuner payload."""

    def __init__(
        self,
        *,
        workflow_run_id: int,
        call_type: str,
        asr_model: str = "",
        llm_model: str = "",
        tts_model: str = "",
        agent_version: int | None = None,
        max_frames: int = 500,
    ) -> None:
        super().__init__()
        self._call_id = str(workflow_run_id)
        self._call_type = call_type
        self._asr_model = asr_model
        self._llm_model = llm_model
        self._tts_model = tts_model
        self._agent_version = agent_version
        self._acc = CallAccumulator()
        self._acc.call_start_abs_ns = time.time_ns()
        self._pipeline_start_rel_ns: int | None = None
        self._context_provider: Callable[[], list[dict[str, Any]]] | None = None
        self._processed_frames: set[int] = set()
        self._frame_history: deque[int] = deque(maxlen=max_frames)

    def attach_context(self, provider: Callable[[], list[dict[str, Any]]]) -> None:
        self._context_provider = provider

    def set_disconnection_reason(self, reason: str | None) -> None:
        if reason:
            self._acc.set_disconnection_reason(reason)

    def attach_turn_tracking_observer(
        self, turn_tracker: TurnTrackingObserver | None
    ) -> None:
        if turn_tracker is None:
            return

        @turn_tracker.event_handler("on_turn_started")
        async def _on_turn_started(_tracker: Any, turn_number: int) -> None:
            self._acc.on_turn_started(turn_number, time.time_ns())

        @turn_tracker.event_handler("on_turn_ended")
        async def _on_turn_ended(
            _tracker: Any, turn_number: int, _duration: float, was_interrupted: bool
        ) -> None:
            self._acc.on_turn_ended(turn_number, was_interrupted)

    def attach_latency_observer(
        self, latency_observer: UserBotLatencyObserver | None
    ) -> None:
        if latency_observer is None:
            return

        @latency_observer.event_handler("on_latency_measured")
        async def _on_latency_measured(_observer: Any, latency: float) -> None:
            self._acc.on_latency_measured(latency)

        @latency_observer.event_handler("on_latency_breakdown")
        async def _on_latency_breakdown(_observer: Any, breakdown: Any) -> None:
            self._acc.on_latency_breakdown(breakdown)

    async def on_push_frame(self, data: FramePushed):
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        if data.frame.id in self._processed_frames:
            return

        self._processed_frames.add(data.frame.id)
        self._frame_history.append(data.frame.id)
        if len(self._processed_frames) > len(self._frame_history):
            self._processed_frames = set(self._frame_history)

        frame = data.frame

        # data.timestamp is a pipeline-relative clock (ns since pipeline start).
        # Convert to absolute ns so the accumulator's _rel_ms() works correctly.
        if self._pipeline_start_rel_ns is None:
            self._pipeline_start_rel_ns = data.timestamp
        timestamp_ns = self._acc.call_start_abs_ns + (
            data.timestamp - self._pipeline_start_rel_ns
        )

        if isinstance(frame, StartFrame):
            self._acc.on_start(timestamp_ns)
        elif isinstance(frame, FunctionCallInProgressFrame):
            self._acc.on_function_call_in_progress(frame, timestamp_ns)
        elif isinstance(frame, FunctionCallResultFrame):
            self._acc.on_function_call_result(frame.tool_call_id, timestamp_ns)
        elif isinstance(frame, MetricsFrame):
            self._acc.on_metrics_frame(frame)
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._acc.on_user_started_speaking(timestamp_ns)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._acc.on_user_stopped_speaking(timestamp_ns)
            self._acc.on_user_turn_stopped(timestamp_ns)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._acc.on_bot_started_speaking(timestamp_ns)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._acc.on_bot_stopped(timestamp_ns)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._acc.on_vad_stopped(timestamp_ns)
        elif isinstance(frame, (CancelFrame, EndFrame)):
            self._acc.on_call_end(timestamp_ns)

    def build_payload_snapshot(
        self,
        *,
        recording_url: str = TUNER_RECORDING_PLACEHOLDER,
    ) -> dict[str, Any] | None:
        if self._context_provider is None:
            logger.warning(
                "[tuner] no context provider attached; skipping payload snapshot"
            )
            return None

        transcript = strip_thought_ids_from_messages(list(self._context_provider()))
        payload = build_payload(
            self._acc,
            _PayloadConfig(
                call_id=self._call_id,
                call_type=self._call_type,
                recording_url=recording_url,
                asr_model=self._asr_model,
                llm_model=self._llm_model,
                tts_model=self._tts_model,
                agent_version=self._agent_version,
            ),
            transcript,
        )
        return payload.to_dict()
