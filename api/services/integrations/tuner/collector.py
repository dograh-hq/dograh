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

# Fallbacks, in preference order, for when the SIP Call-ID is not on the
# session object. ``CID`` is the same value Cloudonix puts in ``callIds``,
# carried as a header. The correlation ids after it are Tuner's own marker,
# written onto the INVITE as ``X-Correlation-Id`` and arriving with the ``X-``
# stripped — a weaker link than the Call-ID, since only Tuner sets it.
_SIP_CALL_ID_HEADER_NAMES: tuple[str, ...] = (
    "cid",
    "call-id",
    "correlation-id",
    "x-correlation-id",
)

# Where Cloudonix puts the custom SIP headers it received, by call origin:
# ``trunk-sip-headers`` for calls arriving at the border or over a trunk,
# ``subscriber-sip-headers`` for calls placed by a domain subscriber.
_SIP_HEADER_PROFILE_FIELDS: tuple[str, ...] = (
    "trunk-sip-headers",
    "subscriber-sip-headers",
)

# Only correlation identifiers leave the deployment. The same field also carries
# provider authentication, source addresses and upstream account ids
# (``Cloudonix-Signature``, ``Cloudonix-IP``, ``Twilio-AccountSid``), none of
# which Tuner correlates on — so forwarding the dict wholesale would disclose
# provider and tenant metadata for no benefit.
_EXPORTABLE_SIP_HEADER_NAMES: frozenset[str] = frozenset(_SIP_CALL_ID_HEADER_NAMES)


def _first_str(value: Any) -> str | None:
    """First non-empty string in a list, or the value itself if it is one."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                return item
    return None


def extract_inbound_sip_metadata(
    workflow_run: Any,
) -> tuple[str | None, dict[str, str] | None]:
    """Recover the SIP Call-ID of an inbound call, and its forwarded headers.

    Tuner links a call we report back to the simulation that placed it by
    matching the caller's SIP Call-ID. Cloudonix keeps it off the
    TwiML-compatible fields — ``CallSid`` and ``Session`` are both the Cloudonix
    session token — but exposes it on the session object, identically in
    ``callIds``, ``profile.callId``, and a ``CID`` header. Verified against live
    calls: the value read here is the one the caller recorded for the same call.

    The webhook body is already persisted by the inbound dispatcher, so nothing
    has to be captured at call time. A call with no Call-ID to recover yields
    ``(None, None)``, and the payload the SDK builds then omits both fields,
    leaving delivery unchanged.
    """
    logs = getattr(workflow_run, "logs", None) or {}
    inbound = logs.get("inbound_webhook") or {}
    raw_webhook_data = inbound.get("raw_webhook_data") or {}

    session_data = raw_webhook_data.get("SessionData")
    if not isinstance(session_data, dict):
        return None, None
    profile = session_data.get("profile")
    profile = profile if isinstance(profile, dict) else {}

    headers: dict[str, str] = {}
    for field in _SIP_HEADER_PROFILE_FIELDS:
        forwarded = profile.get(field)
        if not isinstance(forwarded, dict):
            continue
        for name, value in forwarded.items():
            if value is None or str(name).lower() not in _EXPORTABLE_SIP_HEADER_NAMES:
                continue
            headers.setdefault(str(name), str(value))

    lowered = {name.lower(): value for name, value in headers.items()}
    sip_call_id = (
        _first_str(session_data.get("callIds"))
        or _first_str(profile.get("callId"))
        or next(
            (lowered[name] for name in _SIP_CALL_ID_HEADER_NAMES if lowered.get(name)),
            None,
        )
    )

    # The surviving correlation headers are reported even when no id was
    # recovered from them: they reach Tuner as ``sip_headers`` and show which of
    # them the provider forwarded, so an unlinked call stays diagnosable.
    return sip_call_id, headers or None


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
        sip_call_id: str | None = None,
        sip_headers: dict[str, str] | None = None,
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
            sip_call_id=sip_call_id,
            sip_headers=sip_headers,
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
