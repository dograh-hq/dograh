"""Leave a configured message on an answering machine, then hang up.

The native ``VoicemailDetector`` classifies the callee's first turn and fires
``on_voicemail_detected`` the instant a machine is recognised — usually while
the greeting is still playing. Until now the only handler ended the call on
the spot. This module adds the other thing an outbound dialer needs to do at a
mailbox: wait for the greeting (and the beep) to finish, speak a configured
message once, and *then* hang up — with a disposition that says the message
was actually delivered rather than merely that a machine answered.

Configuration lives next to the detector's own settings, under
``workflow_configurations.voicemail_detection.leave_message``::

    {
      "enabled": true,
      "text": "Hi {{first_name | there}}, this is Ava from Acme …",
      "greeting_end_silence_secs": 2.0,   # optional
      "max_greeting_wait_secs": 30.0,     # optional
      "playback_timeout_secs": 60.0       # optional
    }

``text`` is rendered with the run's call context — the same ``{{…}}``
template variables prompts and greetings use — so a message can name the
contact. When ``enabled`` is false or ``text`` is empty the call ends
immediately on detection, exactly as before.

Why "silence" rather than a beep detector: the pipeline has no tone detector,
and a mailbox greeting is followed by the beep within a second or two of the
recorded voice stopping. Waiting for the far end to be quiet for
``greeting_end_silence_secs`` lands the message after the beep on the
overwhelming majority of mailboxes without any audio-analysis machinery.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.enums import EndTaskReason

from api.services.workflow.end_reasons import VOICEMAIL_MESSAGE_LEFT

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class VoicemailMessageConfig:
    """What to do at a mailbox once the detector has spoken."""

    enabled: bool = False
    text: str = ""
    # Quiet period after the greeting's last speech before the message starts.
    greeting_end_silence_secs: float = 2.0
    # Give up (and just hang up) if the far end never goes quiet — an IVR loop.
    max_greeting_wait_secs: float = 30.0
    # Give up waiting for the message's own playback to finish.
    playback_timeout_secs: float = 60.0

    @classmethod
    def from_voicemail_config(
        cls, voicemail_config: Optional[dict[str, Any]]
    ) -> "VoicemailMessageConfig":
        raw = (voicemail_config or {}).get("leave_message")
        if not isinstance(raw, dict):
            return cls()
        text = raw.get("text")
        return cls(
            enabled=bool(raw.get("enabled", False)),
            text=text.strip() if isinstance(text, str) else "",
            greeting_end_silence_secs=_positive_float(
                raw.get("greeting_end_silence_secs"), cls.greeting_end_silence_secs
            ),
            max_greeting_wait_secs=_positive_float(
                raw.get("max_greeting_wait_secs"), cls.max_greeting_wait_secs
            ),
            playback_timeout_secs=_positive_float(
                raw.get("playback_timeout_secs"), cls.playback_timeout_secs
            ),
        )

    @property
    def active(self) -> bool:
        """True when there is a message to leave."""
        return self.enabled and bool(self.text)


class UserSpeechMonitor(FrameProcessor):
    """Tracks whether the far end is producing speech, so a later step can wait
    for it to stop.

    Sits right after STT. Speech boundaries arrive from two places — the
    transport's VAD (``VADUser*SpeakingFrame``) and the user aggregator's turn
    strategies downstream (``User*SpeakingFrame``, which it also broadcasts
    upstream) — and transcript frames prove speech content is still arriving
    even when a boundary frame was missed. All of them count as activity.
    Every frame is passed through untouched.
    """

    _STARTED = (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)
    _STOPPED = (UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)
    _ACTIVITY = (TranscriptionFrame, InterimTranscriptionFrame)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._speaking = False
        self._last_activity: Optional[float] = None

    @property
    def speaking(self) -> bool:
        return self._speaking

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, self._STARTED):
            self._speaking = True
            self._last_activity = self._now()
        elif isinstance(frame, self._STOPPED):
            self._speaking = False
            self._last_activity = self._now()
        elif isinstance(frame, self._ACTIVITY):
            self._last_activity = self._now()
        await self.push_frame(frame, direction)

    async def wait_for_silence(
        self, quiet_secs: float, *, timeout: float, poll_secs: float = 0.1
    ) -> bool:
        """Resolve True once the far end has been quiet for ``quiet_secs``:
        not mid-utterance, and no boundary or transcript activity in that
        window. ``timeout`` bounds the whole wait; False means the far end
        never went quiet (the caller should not speak over it)."""
        deadline = self._now() + timeout
        if self._last_activity is None:
            # Nothing heard yet: start the quiet clock now rather than
            # treating "no data" as instant silence.
            self._last_activity = self._now()
        while True:
            now = self._now()
            if not self._speaking and now - self._last_activity >= quiet_secs:
                return True
            if now >= deadline:
                return False
            await asyncio.sleep(poll_secs)

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()


async def handle_voicemail_detected(
    engine: "PipecatEngine",
    monitor: Optional[UserSpeechMonitor],
    config: VoicemailMessageConfig,
    *,
    workflow_run_id: int,
) -> str:
    """Runs once the native detector has classified the far end as a machine.

    Returns the reason the call was ended with: ``voicemail_message_left`` when
    the configured message played to completion, otherwise
    ``EndTaskReason.VOICEMAIL_DETECTED`` — the same outcome as before this
    feature existed, so a mailbox that never goes quiet or a TTS that never
    starts degrades to the old behaviour rather than to a half-left message
    reported as delivered.
    """
    engine.mark_voicemail_detected()
    detected = EndTaskReason.VOICEMAIL_DETECTED.value

    if not config.active or monitor is None:
        logger.info(f"[run {workflow_run_id}] voicemail detected; ending call")
        await engine.end_call_with_reason(detected, abort_immediately=True)
        return detected

    logger.info(
        f"[run {workflow_run_id}] voicemail detected; waiting for the greeting "
        f"to end (quiet {config.greeting_end_silence_secs}s) before leaving the message"
    )
    quiet = await monitor.wait_for_silence(
        config.greeting_end_silence_secs, timeout=config.max_greeting_wait_secs
    )
    if not quiet:
        logger.warning(
            f"[run {workflow_run_id}] far end never went quiet within "
            f"{config.max_greeting_wait_secs}s; hanging up without the message"
        )
        await engine.end_call_with_reason(detected, abort_immediately=True)
        return detected

    text = engine.render_call_text(config.text)
    engine.arm_speech_playback()
    await engine.task.queue_frame(
        TTSSpeakFrame(text, append_to_context=False, persist_to_logs=True)
    )
    played = await engine.wait_for_speech_playback(
        start_timeout=5.0, playback_timeout=config.playback_timeout_secs
    )
    if not played:
        stage = "finish" if engine.speech_playback_started else "start"
        logger.warning(
            f"[run {workflow_run_id}] voicemail message did not {stage} playing; "
            "recording the call as voicemail, not as message left"
        )
        await engine.end_call_with_reason(detected)
        return detected

    logger.info(f"[run {workflow_run_id}] voicemail message left ({len(text)} chars)")
    await engine.end_call_with_reason(VOICEMAIL_MESSAGE_LEFT)
    return VOICEMAIL_MESSAGE_LEFT
