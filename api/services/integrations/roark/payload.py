"""Build the Roark ``POST /v1/call`` payload from a finished workflow run.

Roark ingests a completed call as a recording plus the turns and tool calls
that produced it. Everything needed is already on the persisted run by the
time post-call integrations execute, so this package has no live-call
collector: ``workflow_run.logs["realtime_feedback_events"]`` holds the
transcript and function calls, ``gathered_context`` holds the outcome, and
``recording_url`` holds the audio. See the package docstring in
``__init__.py``.

Every function here is pure. The only I/O in this integration is the single
POST in ``client.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable

from pipecat.utils.enums import EndTaskReason, RealtimeFeedbackType

from api.enums import CallType, WorkflowRunMode

# Modes that are browser/app sessions rather than telephony. Roark records this
# as the call's interface type.
_WEB_MODES = {
    WorkflowRunMode.WEBRTC.value,
    WorkflowRunMode.SMALLWEBRTC.value,
}

# Text chat is not a call: it has no recording, and Roark models it with a
# separate chat resource. Runs in this mode are skipped rather than exported.
TEXT_MODES = {WorkflowRunMode.TEXTCHAT.value, WorkflowRunMode.CHAT.value}

# Dograh's end-of-call reason -> Roark's `endedStatus`. Reasons with no honest
# Roark equivalent are left out on purpose: `endedStatus` is omitted rather
# than guessed, and the raw Dograh value always ships as a call property.
_ENDED_STATUS_BY_DISPOSITION = {
    EndTaskReason.CALL_DURATION_EXCEEDED.value: "MAX_DURATION_REACHED",
    EndTaskReason.CALL_TRANSFERRED.value: "AGENT_TRANSFERRED_CALL",
    EndTaskReason.TRANSFER_CALL.value: "AGENT_TRANSFERRED_CALL",
    EndTaskReason.END_CALL.value: "AGENT_ENDED_CALL",
    EndTaskReason.VOICEMAIL_DETECTED.value: "VOICE_MAIL_REACHED",
    EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value: "SILENCE_TIME_OUT",
    EndTaskReason.USER_HANGUP.value: "CUSTOMER_ENDED_CALL",
    EndTaskReason.UNEXPECTED_ERROR.value: "AGENT_ERROR",
    EndTaskReason.PIPELINE_ERROR.value: "AGENT_ERROR",
}

# Roark matches a transcript turn to a participant by label. One customer is
# sent per call, so the label only has to be stable within the payload.
CUSTOMER_LABEL = "Customer"

# `startOffsetMs` is a non-negative int in Roark's schema, so an event that
# somehow predates the anchor is clamped rather than rejected by the API.
_MIN_OFFSET_MS = 0

# Gathered-context keys that already ship as their own call property.
_DISPOSITION_CONTEXT_KEYS = frozenset({"call_disposition", "mapped_call_disposition"})


def mode_to_interface_type(mode: str | None) -> str:
    """Roark's `interfaceType`: a browser session is WEB, a dialled call PHONE."""
    return "WEB" if mode in _WEB_MODES else "PHONE"


def call_type_to_direction(call_type: str | None) -> str:
    """Roark's `callDirection`. Dograh defaults a run to outbound, so does this."""
    return "INBOUND" if call_type == CallType.INBOUND.value else "OUTBOUND"


def disposition_to_ended_status(disposition: str | None) -> str | None:
    """Roark's `endedStatus`, or None when Dograh's reason has no equivalent."""
    if not disposition:
        return None
    return _ENDED_STATUS_BY_DISPOSITION.get(disposition)


def parse_event_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 stamp written by `stamp_realtime_feedback_event`.

    Stamps are always UTC-aware today. A naive one is read as UTC anyway, so a
    single legacy or hand-written event cannot make the whole run's timestamps
    incomparable and crash the export.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _event_start(event: dict[str, Any]) -> datetime | None:
    payload = event.get("payload") or {}
    return parse_event_timestamp(event.get("timestamp")) or parse_event_timestamp(
        payload.get("timestamp")
    )


def _event_end(event: dict[str, Any]) -> datetime | None:
    payload = event.get("payload") or {}
    return parse_event_timestamp(payload.get("end_timestamp"))


def resolve_anchor(events: Iterable[dict[str, Any]]) -> datetime | None:
    """The earliest stamped event, used as the call's t=0.

    Dograh does not record when the recording started, so the first thing that
    happened in the pipeline is the closest available anchor. Every offset in
    the payload is measured from it and `startedAt` is set to it, which keeps
    the transcript self-consistent; it can sit a fraction of a second after the
    true start of the audio.
    """
    starts = [start for start in (_event_start(e) for e in events) if start]
    return min(starts) if starts else None


def _as_utc(moment: datetime | None) -> datetime:
    """A UTC-aware anchor, falling back to now for a run with no usable clock.

    A run always has `created_at`, so the fallback only fires for a hand-built
    row in a test. It keeps the payload builder total rather than raising.
    """
    if moment is None:
        return datetime.now(UTC)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _offset_ms(moment: datetime | None, anchor: datetime) -> int | None:
    if moment is None:
        return None
    return max(_MIN_OFFSET_MS, int((moment - anchor).total_seconds() * 1000))


def build_transcript(
    events: Iterable[dict[str, Any]],
    anchor: datetime,
) -> list[dict[str, Any]]:
    """Dograh's realtime feedback events as Roark transcript entries.

    Only final user transcriptions and bot text become turns, matching what
    `generate_transcript_text` writes to the stored transcript artifact. Agent
    and customer metadata is left off every entry: the call carries exactly one
    of each, and Roark attributes an unlabelled turn to the single participant
    of that role.
    """
    entries: list[dict[str, Any]] = []

    for event in events:
        event_type = event.get("type")
        payload = event.get("payload") or {}

        if event_type == RealtimeFeedbackType.USER_TRANSCRIPTION.value:
            if payload.get("final") is not True:
                continue
            role = "CUSTOMER"
        elif event_type == RealtimeFeedbackType.BOT_TEXT.value:
            role = "AGENT"
        else:
            continue

        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        start_offset_ms = _offset_ms(_event_start(event), anchor)
        if start_offset_ms is None:
            continue
        end_offset_ms = _offset_ms(_event_end(event), anchor)

        entry: dict[str, Any] = {
            "role": role,
            "text": text,
            "startOffsetMs": start_offset_ms,
            # Roark requires an end offset on every turn. Dograh only records
            # one when the provider reported it, so an unknown end collapses the
            # turn to an instant rather than inventing a duration: Roark extends
            # an implausibly short turn to the next turn's start on ingest, which
            # is a better guess than anything computable here.
            "endOffsetMs": max(end_offset_ms or 0, start_offset_ms),
        }
        if role == "CUSTOMER":
            entry["customer"] = {"label": CUSTOMER_LABEL}
        entries.append(entry)

    return entries


def build_tool_invocations(
    events: Iterable[dict[str, Any]],
    anchor: datetime,
) -> list[dict[str, Any]]:
    """Function calls the agent made, paired start-to-end by `tool_call_id`.

    A call whose end was never logged (the pipeline stopped mid-flight) is
    still reported, without an `endOffsetMs` and with an empty result. That a
    tool was invoked and never returned is the signal, so dropping it would
    hide the more interesting case.
    """
    ends_by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("type") != RealtimeFeedbackType.FUNCTION_CALL_END.value:
            continue
        tool_call_id = (event.get("payload") or {}).get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id:
            ends_by_id[tool_call_id] = event

    invocations: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != RealtimeFeedbackType.FUNCTION_CALL_START.value:
            continue

        payload = event.get("payload") or {}
        name = payload.get("function_name")
        if not isinstance(name, str) or not name:
            continue

        start_offset_ms = _offset_ms(_event_start(event), anchor)
        if start_offset_ms is None:
            continue

        arguments = payload.get("arguments")
        invocation: dict[str, Any] = {
            "name": name,
            "parameters": arguments if isinstance(arguments, dict) else {},
            "result": "",
            "startOffsetMs": start_offset_ms,
        }

        tool_call_id = payload.get("tool_call_id")
        end_event = (
            ends_by_id.get(tool_call_id) if isinstance(tool_call_id, str) else None
        )
        if end_event is not None:
            result = (end_event.get("payload") or {}).get("result")
            invocation["result"] = result if isinstance(result, str) else ""
            end_offset_ms = _offset_ms(_event_start(end_event), anchor)
            if end_offset_ms is not None:
                invocation["endOffsetMs"] = max(end_offset_ms, start_offset_ms)

        invocations.append(invocation)

    return invocations


def build_agent(
    *,
    agent_id: str | None,
    agent_name: str | None,
) -> dict[str, Any]:
    """Roark's agent identification.

    A UUID attaches the call to exactly that agent. A name instead reuses the
    agent with that exact name in the project, or creates it on the first call,
    which is what a Dograh user gets without any setup in Roark.
    """
    if agent_id and agent_id.strip():
        return {"roarkId": agent_id.strip()}
    return {"name": (agent_name or "").strip()}


def build_customer(
    initial_context: dict[str, Any] | None,
    *,
    direction: str,
) -> dict[str, Any]:
    """The far end of the call: the caller inbound, the number dialled outbound."""
    context = initial_context or {}
    if direction == "INBOUND":
        phone_number = context.get("caller_number")
    else:
        phone_number = context.get("phone_number") or context.get("called_number")

    return {
        "label": CUSTOMER_LABEL,
        "phoneNumberE164": phone_number if isinstance(phone_number, str) else None,
    }


def build_properties(
    *,
    workflow_run: Any,
    definition_id: int | None,
    gathered_context: dict[str, Any],
    include_gathered_context: bool,
) -> dict[str, Any]:
    """Call properties Roark can filter and group on.

    A fixed set of Dograh run identifiers always ships. The variables the agent
    gathered are opt-in because they routinely hold personal data, and they are
    namespaced so they cannot shadow the identifiers above them.

    Keys are snake_case because Roark stores them that way: it snake-cases every
    property name on ingest, so sending camelCase would mean the names a user
    filters on in Roark never match the names written here.
    """
    workflow = getattr(workflow_run, "workflow", None)
    properties: dict[str, Any] = {
        "dograh_workflow_run_id": workflow_run.id,
        "dograh_workflow_id": getattr(workflow_run, "workflow_id", None),
        "dograh_run_mode": getattr(workflow_run, "mode", None),
    }
    if workflow is not None and getattr(workflow, "name", None):
        properties["dograh_workflow_name"] = workflow.name
    if definition_id is not None:
        properties["dograh_definition_id"] = definition_id
    if getattr(workflow_run, "campaign_id", None) is not None:
        properties["dograh_campaign_id"] = workflow_run.campaign_id

    disposition = gathered_context.get("call_disposition")
    if disposition:
        properties["dograh_call_disposition"] = disposition
    mapped_disposition = gathered_context.get("mapped_call_disposition")
    if mapped_disposition:
        properties["dograh_mapped_call_disposition"] = mapped_disposition

    if include_gathered_context:
        for key, value in gathered_context.items():
            # Both dispositions already ship above under their own names; a
            # prefixed copy would only be a second column holding the same value.
            if key in _DISPOSITION_CONTEXT_KEYS:
                continue
            properties[f"dograh_context_{key}"] = value

    return properties


def build_call_payload(
    *,
    workflow_run: Any,
    definition_id: int | None,
    recording_url: str,
    agent_id: str | None,
    agent_name: str | None,
    send_transcript: bool,
    send_gathered_context: bool,
) -> dict[str, Any]:
    """The full `POST /v1/call` body for one finished run."""
    events = _sorted_events(workflow_run)
    gathered_context = getattr(workflow_run, "gathered_context", None) or {}

    anchor = _as_utc(
        resolve_anchor(events) or getattr(workflow_run, "created_at", None)
    )
    direction = call_type_to_direction(getattr(workflow_run, "call_type", None))

    payload: dict[str, Any] = {
        "recordingUrl": recording_url,
        "startedAt": anchor.isoformat(),
        "interfaceType": mode_to_interface_type(getattr(workflow_run, "mode", None)),
        "callDirection": direction,
        "agent": build_agent(agent_id=agent_id, agent_name=agent_name),
        "customer": build_customer(
            getattr(workflow_run, "initial_context", None), direction=direction
        ),
        "properties": build_properties(
            workflow_run=workflow_run,
            definition_id=definition_id,
            gathered_context=gathered_context,
            include_gathered_context=send_gathered_context,
        ),
        # Lets a Roark user jump from a call back to the Dograh run, and makes
        # a redelivery of the same run collide instead of creating a second
        # call: `externalId` is unique within a Roark project.
        "externalId": f"dograh-run-{workflow_run.id}",
    }

    ended_status = disposition_to_ended_status(gathered_context.get("call_disposition"))
    if ended_status:
        payload["endedStatus"] = ended_status

    if send_transcript:
        transcript = build_transcript(events, anchor)
        if transcript:
            payload["transcript"] = transcript
        tool_invocations = build_tool_invocations(events, anchor)
        if tool_invocations:
            payload["toolInvocations"] = tool_invocations

    return payload


def _sorted_events(workflow_run: Any) -> list[dict[str, Any]]:
    """Persisted realtime feedback events, oldest first.

    They are written in order by `InMemoryLogsBuffer.get_events`, but the
    payload's offsets depend on reading them in time order, so this does not
    rely on that.
    """
    logs = getattr(workflow_run, "logs", None) or {}
    events = logs.get("realtime_feedback_events")
    if not isinstance(events, list):
        return []
    typed = [event for event in events if isinstance(event, dict)]
    unstamped = datetime.max.replace(tzinfo=UTC)
    return sorted(typed, key=lambda event: _event_start(event) or unstamped)
