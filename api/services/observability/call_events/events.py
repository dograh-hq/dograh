"""Event model for pipeline diagnostics.

One row per observable fact about a call: who spoke, when the turn was
released, whether the bot answered, why it did not. The names and the
``detail`` keys defined here are a contract — they are what downstream
analytics queries, and must remain stable across sink implementations. Renaming
a key silently breaks a dashboard, so add rather than rename.

``detail`` must stay small (~1 KB) and JSON-serialisable. It never carries
audio or transcript text: for anything textual only the length is recorded.
"""

import os
import socket
import time
from dataclasses import dataclass, field
from uuid import uuid4

# Severities. Anything a human should look at is warn; error is reserved for
# failures the pipeline itself reported.
SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_ERROR = "error"

# Speech and transcription.
BOT_SPEAKING_START = "bot_speaking_start"
BOT_SPEAKING_STOP = "bot_speaking_stop"
USER_SPEAKING_START = "user_speaking_start"
USER_SPEAKING_STOP = "user_speaking_stop"
TRANSCRIPT_FINAL = "transcript_final"

# Turn taking.
USER_TURN_STARTED = "user_turn_started"
USER_TURN_STOPPED = "user_turn_stopped"
USER_TURN_STOP_TIMEOUT = "user_turn_stop_timeout"
USER_TURN_IDLE = "user_turn_idle"
MUTE_STARTED = "mute_started"
MUTE_STOPPED = "mute_stopped"

# Generation.
FUNCTION_CALL_STARTED = "function_call_started"
FUNCTION_CALL_ENDED = "function_call_ended"
FUNCTION_CALL_HUNG = "function_call_hung"
LLM_RESPONSE_EMPTY = "llm_response_empty"
REFUSAL_DROPPED = "refusal_dropped"
TTS_STARTED = "tts_started"
TTS_STOPPED = "tts_stopped"
BOT_SILENT_AFTER_USER_TURN = "bot_silent_after_user_turn"

# Latency and lifecycle.
LATENCY_BREAKDOWN = "latency_breakdown"
FIRST_BOT_SPEECH_LATENCY = "first_bot_speech_latency"
PIPELINE_ERROR = "pipeline_error"
HEARTBEAT_TIMEOUT = "heartbeat_timeout"
PIPELINE_IDLE_TIMEOUT = "pipeline_idle_timeout"
INTERRUPTION = "interruption"
CALL_ENDED = "call_ended"


def host_name() -> str:
    """Which host ran this pipeline, reported as ``call_ended.detail.host``.

    Multi-host installations set ``DOGRAH_INSTANCE`` to a stable instance name
    so latencies and outcomes can be compared per host; otherwise the hostname
    identifies it.
    """
    return os.environ.get("DOGRAH_INSTANCE") or socket.gethostname()


@dataclass(slots=True, kw_only=True)
class CallEvent:
    """A destination-independent diagnostic fact. Identity is assigned once."""

    event: str
    ts: float = field(default_factory=time.time)
    run_id: int | None = None
    org_id: int | None = None
    workflow_id: int | None = None
    turn: int | None = None
    severity: str = SEVERITY_INFO
    value_ms: float | None = None
    node_id: str | None = None
    node_name: str | None = None
    detail: dict = field(default_factory=dict)

    event_id: str = field(default_factory=lambda: uuid4().hex)

    def render(self) -> str:
        """One-line rendering used for the LOG_LEVEL=DEBUG mirror of the stream."""
        value = "" if self.value_ms is None else f" {self.value_ms:.0f}ms"
        return (
            f"[diag] run={self.run_id} turn={self.turn} {self.event}{value} "
            f"{self.detail}"
        )
