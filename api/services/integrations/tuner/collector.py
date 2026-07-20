from __future__ import annotations

from typing import Any

from tuner_pipecat_sdk import Observer

from api.enums import WorkflowRunMode

TUNER_RECORDING_PLACEHOLDER = "pipecat://no-recording"

# Placeholder credentials for the SDK Observer's TunerConfig. Real BYOK credentials
# (api_key / workspace_id / agent_id) are per tuner node and are applied later during
# the deferred delivery phase (completion.py), so they are not known here. TunerConfig
# validators require a non-empty api_key/agent_id and a positive workspace_id, hence
# these placeholders.
_DEFERRED_API_KEY = "deferred"
_DEFERRED_WORKSPACE_ID = 1
_DEFERRED_AGENT_ID = "deferred"


def mode_to_tuner_call_type(mode: str | None) -> str:
    if mode in {
        WorkflowRunMode.WEBRTC.value,
        WorkflowRunMode.SMALLWEBRTC.value,
    }:
        return "web_call"
    return "phone_call"


class DeferredTunerObserver(Observer):
    """SDK ``Observer`` that builds the Tuner payload from the live frame stream but
    defers delivery to the completion phase instead of POSTing on call end.

    The SDK ``Observer`` normally fire-and-forgets ``post_call`` when the call ends.
    Dograh instead snapshots the payload into ``workflow_run.logs`` and delivers it
    later (``completion.py``) — once per tuner node with that node's BYOK credentials,
    after injecting the real ``recording_url`` and a locally-computed ``call_cost``.
    """

    def __init__(
        self,
        *,
        workflow_run_id: int,
        call_type: str,
        asr_model: str = "",
        llm_model: str = "",
        tts_model: str = "",
        agent_version: int | None = None,
    ) -> None:
        super().__init__(
            api_key=_DEFERRED_API_KEY,
            workspace_id=_DEFERRED_WORKSPACE_ID,
            agent_id=_DEFERRED_AGENT_ID,
            call_id=str(workflow_run_id),
            call_type=call_type,
            recording_url=TUNER_RECORDING_PLACEHOLDER,
            asr_model=asr_model,
            llm_model=llm_model,
            tts_model=tts_model,
            agent_version=agent_version,
        )

    async def _flush(self) -> None:
        # Suppress the SDK's runtime post_call; delivery is deferred (see class docstring).
        return None

    def set_disconnection_reason(self, reason: str | None) -> None:
        if reason:
            self._acc.set_disconnection_reason(reason)

    def build_payload_snapshot(
        self,
        *,
        recording_url: str = TUNER_RECORDING_PLACEHOLDER,
    ) -> dict[str, Any] | None:
        self._config.recording_url = recording_url
        payload = self._acc.build_payload(self._config, None)
        return payload.to_dict()
