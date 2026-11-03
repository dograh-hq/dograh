"""Answer handling configuration stored inside workflow voicemail_detection."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

DEFAULT_LISTENING_WINDOW_SECONDS = 1.2
_DELAYED_START_SECONDS = TypeAdapter(
    Annotated[float, Field(ge=0, le=10, allow_inf_nan=False, strict=True)]
)


def resolve_listening_window_ms(node, *, default: int = 0) -> int:
    """Resolve Delayed Start independently of pipeline and classification."""
    if node is None or not getattr(node, "delayed_start", False):
        return default
    duration = getattr(node, "delayed_start_duration", None)
    # Keep the existing zero-delay setting valid, but apply the same finite,
    # bounded duration contract even when answer supervision is disabled.
    duration = _DELAYED_START_SECONDS.validate_python(
        DEFAULT_LISTENING_WINDOW_SECONDS if duration is None else duration
    )
    return round(duration * 1000)


class AnswerMessage(BaseModel):
    text: str = Field(default="", max_length=5000)
    recording_id: str | None = None
    recording_pk: int | None = Field(default=None, gt=0)

    @property
    def configured(self) -> bool:
        return bool(self.text.strip() or self.recording_id or self.recording_pk)


class AnswerSupervisorConfig(BaseModel):
    """Shared opening policy with optional machine detection; times are in ms."""

    # The containing object also holds the existing classifier credentials.
    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(
        default=True,
        description="Classify opening speech for voicemail and screening.",
    )
    listening_window_ms: int = Field(
        default=round(DEFAULT_LISTENING_WINDOW_SECONDS * 1000),
        ge=0,
        le=10000,
        description=(
            "Initial wait for speech after arming; silence releases opening. "
            "Resolved from the Start node's Delayed Start setting."
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
    is_realtime: bool,
    start_node,
) -> AnswerSupervisorConfig | None:
    """Use the same opening flow for every inbound and outbound cascade call.

    The voicemail toggle only enables classification. Delayed Start owns timing
    in both modes, including zero wait when disabled. Ignore legacy detector
    listening windows and inactive classification settings.
    """
    if is_realtime:
        return None
    values = (
        dict(voicemail_config)
        if voicemail_config.get("enabled", False)
        else {"enabled": False}
    )
    values["listening_window_ms"] = resolve_listening_window_ms(start_node)
    return AnswerSupervisorConfig.model_validate(values)
