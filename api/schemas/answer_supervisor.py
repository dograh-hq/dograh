"""Answer handling configuration stored inside workflow voicemail_detection."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_LISTENING_WINDOW_SECONDS = 1.2


class AnswerMessage(BaseModel):
    text: str = Field(default="", max_length=5000)
    recording_id: str | None = None
    recording_pk: int | None = Field(default=None, gt=0)

    @property
    def configured(self) -> bool:
        return bool(self.text.strip() or self.recording_id or self.recording_pk)


class AnswerSupervisorConfig(BaseModel):
    """Outbound answer policy: times in ms; recordings take precedence over text."""

    # The containing object also holds the existing classifier credentials.
    model_config = ConfigDict(extra="ignore")

    listening_window_ms: int = Field(
        default=round(DEFAULT_LISTENING_WINDOW_SECONDS * 1000),
        ge=0,
        le=10000,
        description=(
            "Initial wait for speech after arming; silence releases opening. "
            "Enabled Delayed Start overrides."
        ),
    )
    human_utterance_max_ms: int = Field(
        default=2500,
        gt=0,
        le=10000,
        description=(
            "Nonempty resolved turn below threshold is human; otherwise classify. "
            "Duration includes pauses."
        ),
    )
    machine_utterance_cap_ms: int = Field(
        default=25000,
        gt=0,
        le=60000,
        description=(
            "Wait from resolved user turn start (or arming, if later) for usable "
            "turn completion; expiry disconnects the call."
        ),
    )
    classify_budget_ms: int = Field(
        default=3000,
        gt=0,
        le=10000,
        description=(
            "LLM fallback budget after unmatched patterns; timeout yields UNKNOWN "
            "(release initially, wait in screening)."
        ),
    )
    screening_wait_ms: int = Field(
        default=30000,
        gt=0,
        le=60000,
        description=(
            "Wait after screening playback; expiry hangs up after any active turn "
            "finishes within the utterance and classification budgets."
        ),
    )
    max_screening_rearms: int = Field(
        default=2,
        ge=0,
        le=2,
        description=(
            "Maximum screening announcement/wait attempts; "
            "zero or exhausted limit hangs up on a screener."
        ),
    )
    voicemail_action: Literal["hangup", "leave_message"] = Field(
        default="hangup",
        description="Disconnect or play a message after voicemail is detected.",
    )
    voicemail_message: AnswerMessage = Field(
        default_factory=AnswerMessage,
        description="Text or recording to play when voicemail_action is leave_message.",
    )
    screening_message: AnswerMessage = Field(
        default_factory=AnswerMessage,
        description=(
            "Caller introduction before waiting for a subscriber; empty means hang up."
        ),
    )


def resolve_answer_supervisor_config(
    voicemail_config: dict,
    *,
    call_direction: str | None,
    is_realtime: bool,
    start_node,
) -> AnswerSupervisorConfig | None:
    """Use the supervisor for every enabled outbound cascade workflow.

    The Start node's enabled Delayed Start duration supplies the listening
    window, ahead of a saved window or its 1200 ms default. This is a listening
    deadline, not a separate sleep during node setup.
    """
    if (
        call_direction != "outbound"
        or is_realtime
        or not voicemail_config.get("enabled", False)
    ):
        return None
    values = dict(voicemail_config)
    if start_node and start_node.delayed_start:
        duration = start_node.delayed_start_duration
        # Share the default with the Start node UI.
        values["listening_window_ms"] = round(
            (DEFAULT_LISTENING_WINDOW_SECONDS if duration is None else duration) * 1000
        )
    return AnswerSupervisorConfig.model_validate(values)
