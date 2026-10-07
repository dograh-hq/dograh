"""Unit coverage for the Roark post-call export.

The integration has no live-call collector, so everything it does is a pure
transform of a persisted run plus one POST. These tests exercise the transform
directly and stub the POST; `test_roark_e2e.py` runs the same handler against a
real Roark API.
"""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from api.services.configuration.masking import mask_key, mask_workflow_definition
from api.services.integrations.base import IntegrationCompletionContext
from api.services.integrations.registry import (
    get_node_registration,
    get_node_secret_fields,
)
from api.services.integrations.roark.client import (
    RoarkDeliveryConfig,
    RoarkDeliveryError,
    create_call,
)
from api.services.integrations.roark.completion import (
    build_recording_url,
    run_completion,
)
from api.services.integrations.roark.node import RoarkNodeData
from api.services.integrations.roark.payload import (
    CUSTOMER_LABEL,
    build_call_payload,
    build_customer,
    build_tool_invocations,
    build_transcript,
    call_type_to_direction,
    disposition_to_ended_status,
    mode_to_interface_type,
    resolve_anchor,
)
from api.services.workflow.node_specs import all_specs

# `create_call` builds its own client, so the transport is swapped underneath it.
# The real class is captured here because patching the attribute replaces it on
# the shared `httpx` module, and a factory that looked it up again would recurse.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


@contextmanager
def _roark_http(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=transport, **kwargs)

    with patch("api.services.integrations.roark.client.httpx.AsyncClient", factory):
        yield


T0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)


def _stamp(offset_seconds: float) -> str:
    return (T0 + timedelta(seconds=offset_seconds)).isoformat(timespec="milliseconds")


def _bot_event(text, start, end=None):
    payload = {"text": text}
    if end is not None:
        payload["end_timestamp"] = _stamp(end)
    return {
        "type": "rtf-bot-text",
        "timestamp": _stamp(start),
        "payload": payload,
    }


def _user_event(text, start, end=None, final=True):
    payload = {"text": text, "final": final}
    if end is not None:
        payload["end_timestamp"] = _stamp(end)
    return {
        "type": "rtf-user-transcription",
        "timestamp": _stamp(start),
        "payload": payload,
    }


def _tool_start(name, start, tool_call_id="call-1", arguments=None):
    return {
        "type": "rtf-function-call-start",
        "timestamp": _stamp(start),
        "payload": {
            "function_name": name,
            "tool_call_id": tool_call_id,
            "arguments": arguments if arguments is not None else {"city": "Lisbon"},
        },
    }


def _tool_end(name, end, tool_call_id="call-1", result="sunny"):
    return {
        "type": "rtf-function-call-end",
        "timestamp": _stamp(end),
        "payload": {
            "function_name": name,
            "tool_call_id": tool_call_id,
            "result": result,
        },
    }


def _events():
    return [
        _bot_event("Hi, this is Dograh.", 0.0, 1.5),
        _user_event("partial", 2.0, final=False),
        _user_event("Hello there.", 2.0, 2.8),
        _tool_start("get_weather", 3.0),
        _tool_end("get_weather", 3.4),
        _bot_event("It is sunny.", 3.5, 4.2),
    ]


def _workflow_run(**overrides):
    defaults = dict(
        id=4242,
        workflow_id=7,
        workflow=SimpleNamespace(name="Support Bot"),
        mode="twilio",
        call_type="outbound",
        campaign_id=None,
        created_at=T0 - timedelta(seconds=5),
        recording_url="recordings/4242.wav",
        initial_context={"phone_number": "+14155551234", "direction": "outbound"},
        gathered_context={
            "call_disposition": "user_hangup",
            "mapped_call_disposition": "appointment_confirmed",
            "customer_name": "Ada",
        },
        logs={"realtime_feedback_events": _events()},
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _completion_context(workflow_run=None, public_token="tok-123"):
    run = workflow_run if workflow_run is not None else _workflow_run()
    return IntegrationCompletionContext(
        workflow_run_id=run.id,
        workflow_run=run,
        workflow_definition={"nodes": []},
        definition_id=9,
        organization_id=1,
        public_token=public_token,
    )


def _roark_node(node_id="roark-1", api_key="rk_live_secret", **extra):
    data = {
        "name": "Roark",
        "roark_enabled": True,
        "roark_agent_name": "Support Bot",
        **extra,
    }
    if api_key:
        data["roark_api_key"] = api_key
    return {"id": node_id, "type": "roark", "position": {"x": 0, "y": 0}, "data": data}


# ───────────────────────────── node model + spec ──────────────────────────


def test_roark_spec_property_order_stable():
    spec = next(spec for spec in all_specs() if spec.name == "roark")
    assert [prop.name for prop in spec.properties] == [
        "name",
        "roark_enabled",
        "roark_api_key",
        "roark_agent_name",
        "roark_agent_id",
        "roark_send_transcript",
        "roark_send_gathered_context",
    ]


def test_roark_node_is_registered_with_sensitive_api_key():
    registration = get_node_registration("roark")
    assert registration is not None
    assert registration.data_model is RoarkNodeData
    assert get_node_secret_fields("roark") == ("roark_api_key",)


def test_roark_node_is_not_connectable():
    spec = next(spec for spec in all_specs() if spec.name == "roark")
    constraints = spec.graph_constraints
    assert constraints.max_incoming == 0
    assert constraints.max_outgoing == 0
    assert constraints.max_instances == 1


def test_roark_spec_example_validates_against_the_model():
    spec = next(spec for spec in all_specs() if spec.name == "roark")
    for example in spec.examples:
        RoarkNodeData.model_validate(example.data)


def test_enabled_node_requires_api_key_and_an_agent():
    with pytest.raises(ValidationError):
        RoarkNodeData.model_validate({"name": "Roark", "roark_enabled": True})


def test_enabled_node_accepts_an_agent_id_instead_of_a_name():
    data = RoarkNodeData.model_validate(
        {
            "name": "Roark",
            "roark_enabled": True,
            "roark_api_key": "rk_live_x",
            "roark_agent_id": "6d1e0d6e-0f7a-4f77-9b3f-3a1b2c3d4e5f",
        }
    )
    assert data.roark_agent_name is None


def test_disabled_node_validates_without_credentials():
    data = RoarkNodeData.model_validate({"name": "Roark", "roark_enabled": False})
    assert data.roark_enabled is False


def test_masks_roark_api_key():
    real_key = "rk_live_abcdefghijklmnop"
    definition = {"nodes": [_roark_node(api_key=real_key)]}

    masked = mask_workflow_definition(definition)

    masked_key = masked["nodes"][0]["data"]["roark_api_key"]
    assert masked_key == mask_key(real_key)
    assert masked_key.endswith("mnop")


# ───────────────────────────── field mapping ──────────────────────────────


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("webrtc", "WEB"),
        ("smallwebrtc", "WEB"),
        ("twilio", "PHONE"),
        ("plivo", "PHONE"),
        (None, "PHONE"),
    ],
)
def test_mode_to_interface_type(mode, expected):
    assert mode_to_interface_type(mode) == expected


@pytest.mark.parametrize(
    ("call_type", "expected"),
    [("inbound", "INBOUND"), ("outbound", "OUTBOUND"), (None, "OUTBOUND")],
)
def test_call_type_to_direction(call_type, expected):
    assert call_type_to_direction(call_type) == expected


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        ("user_hangup", "CUSTOMER_ENDED_CALL"),
        ("end_call", "AGENT_ENDED_CALL"),
        ("voicemail_detected", "VOICE_MAIL_REACHED"),
        ("call_duration_exceeded", "MAX_DURATION_REACHED"),
        ("transfer_call", "AGENT_TRANSFERRED_CALL"),
        # No honest Roark equivalent: omitted rather than guessed.
        ("system_cancelled", None),
        ("something_new", None),
        (None, None),
    ],
)
def test_disposition_to_ended_status(disposition, expected):
    assert disposition_to_ended_status(disposition) == expected


def test_build_customer_reads_the_caller_on_an_inbound_call():
    customer = build_customer(
        {"caller_number": "+15550001111", "called_number": "+15552223333"},
        direction="INBOUND",
    )
    assert customer == {"label": CUSTOMER_LABEL, "phoneNumberE164": "+15550001111"}


def test_build_customer_reads_the_dialled_number_on_an_outbound_call():
    customer = build_customer(
        {"phone_number": "+15550001111", "direction": "outbound"},
        direction="OUTBOUND",
    )
    assert customer == {"label": CUSTOMER_LABEL, "phoneNumberE164": "+15550001111"}


def test_build_customer_has_a_null_number_for_a_web_call():
    assert build_customer({}, direction="OUTBOUND") == {
        "label": CUSTOMER_LABEL,
        "phoneNumberE164": None,
    }


# ───────────────────────────── transcript ─────────────────────────────────


def test_resolve_anchor_is_the_earliest_event():
    assert resolve_anchor(_events()) == T0


def test_resolve_anchor_is_none_without_events():
    assert resolve_anchor([]) is None


def test_build_transcript_maps_roles_and_offsets():
    entries = build_transcript(_events(), T0)

    assert entries == [
        {
            "role": "AGENT",
            "text": "Hi, this is Dograh.",
            "startOffsetMs": 0,
            "endOffsetMs": 1500,
        },
        {
            "role": "CUSTOMER",
            "text": "Hello there.",
            "startOffsetMs": 2000,
            "endOffsetMs": 2800,
            "customer": {"label": CUSTOMER_LABEL},
        },
        {
            "role": "AGENT",
            "text": "It is sunny.",
            "startOffsetMs": 3500,
            "endOffsetMs": 4200,
        },
    ]


def _logged_turn(kind, text, logged, *, speech_start=None, speech_end=None, final=True):
    """A turn shaped the way Dograh really writes one.

    The top-level stamp is when the event was logged, which is at or after the
    end of the utterance. The provider's own onset, when there is one, is
    `payload.timestamp`.
    """
    payload = {"text": text}
    if kind == "rtf-user-transcription":
        payload["final"] = final
    if speech_start is not None:
        payload["timestamp"] = _stamp(speech_start)
    if speech_end is not None:
        payload["end_timestamp"] = _stamp(speech_end)
    return {"type": kind, "timestamp": _stamp(logged), "payload": payload}


def _tts_first_byte(at):
    return {
        "type": "rtf-ttfb-metric",
        "timestamp": _stamp(at),
        "payload": {"kind": "tts", "ttfb_seconds": 0.3, "processor": "x", "model": "y"},
    }


def test_turn_starts_at_the_spoken_onset_not_when_it_was_logged():
    """The regression this file exists for.

    A bot turn is logged once aggregated, which on a measured call was 5.6s
    after it began speaking. Reading the logged stamp as the start put every
    subtitle seconds late in the player.
    """
    events = [
        _logged_turn(
            "rtf-bot-text",
            "Hello there.",
            14.811,
            speech_start=12.906,
            speech_end=16.267,
        )
    ]

    entries = build_transcript(events, T0)

    assert entries[0]["startOffsetMs"] == 12906
    assert entries[0]["endOffsetMs"] == 16267


def test_customer_turn_also_starts_at_its_spoken_onset():
    events = [
        _logged_turn(
            "rtf-user-transcription",
            "Yes, that works.",
            9.610,
            speech_start=8.565,
            speech_end=9.609,
        )
    ]

    entries = build_transcript(events, T0)

    assert entries[0]["startOffsetMs"] == 8565
    assert entries[0]["endOffsetMs"] == 9609


def test_agent_turn_without_an_onset_falls_back_to_the_tts_first_byte():
    """Dograh records no onset for the opening greeting, and its logged stamp
    sits at the end of the utterance. The synthesizer's first audio byte is
    where that speech actually starts in the recording."""
    events = [
        {
            "type": "rtf-node-transition",
            "timestamp": _stamp(0.0),
            "payload": {"node_name": "Start Call"},
        },
        _tts_first_byte(1.374),
        _logged_turn("rtf-bot-text", "Hello! Calling to confirm.", 6.987),
        _logged_turn(
            "rtf-user-transcription",
            "Yes.",
            9.610,
            speech_start=8.565,
            speech_end=9.609,
        ),
    ]

    entries = build_transcript(events, T0)

    assert entries[0]["startOffsetMs"] == 1374
    # and the end, which Dograh never recorded, reaches the next turn
    assert entries[0]["endOffsetMs"] == 8565


def test_the_tts_fallback_only_looks_backwards():
    """A later utterance's first byte must not be read as this one's start."""
    events = [
        _tts_first_byte(1.0),
        _logged_turn("rtf-bot-text", "First.", 4.0),
        _tts_first_byte(6.0),
        _logged_turn("rtf-bot-text", "Second.", 9.0),
    ]

    entries = build_transcript(events, T0)

    assert [entry["startOffsetMs"] for entry in entries] == [1000, 6000]


def test_a_turn_with_no_end_reaches_the_next_turn():
    events = [
        _logged_turn("rtf-bot-text", "Hi.", 1.0, speech_start=1.0),
        _logged_turn(
            "rtf-user-transcription", "Hello.", 5.0, speech_start=4.0, speech_end=5.0
        ),
    ]

    entries = build_transcript(events, T0)

    assert entries[0]["startOffsetMs"] == 1000
    assert entries[0]["endOffsetMs"] == 4000


def test_the_final_turn_is_not_given_an_invented_end():
    """Nothing follows it, so any end would be a guess. Roark cannot repair it
    either, and a guess would be indistinguishable from a measured value."""
    events = [_logged_turn("rtf-bot-text", "Goodbye.", 3.0, speech_start=3.0)]

    entries = build_transcript(events, T0)

    assert entries[0]["endOffsetMs"] == entries[0]["startOffsetMs"]


def test_turns_are_ordered_by_onset_not_by_when_they_were_logged():
    """An utterance that started earlier can be logged later than a short one
    that followed it, so emitting in log order would hand the player a
    backwards transcript."""
    events = [
        _logged_turn(
            "rtf-user-transcription", "Short.", 4.0, speech_start=3.5, speech_end=4.0
        ),
        _logged_turn(
            "rtf-bot-text",
            "A much longer sentence.",
            9.0,
            speech_start=1.0,
            speech_end=8.9,
        ),
    ]

    entries = build_transcript(events, T0)

    assert [entry["text"] for entry in entries] == ["A much longer sentence.", "Short."]


def test_build_transcript_skips_interim_user_transcriptions():
    entries = build_transcript([_user_event("partial", 1.0, final=False)], T0)
    assert entries == []


def test_build_transcript_skips_empty_text():
    entries = build_transcript([_bot_event("   ", 1.0)], T0)
    assert entries == []


def test_build_transcript_collapses_a_turn_with_no_end_timestamp():
    entries = build_transcript([_bot_event("Hello", 1.0)], T0)
    assert entries[0]["startOffsetMs"] == 1000
    assert entries[0]["endOffsetMs"] == 1000


def test_build_transcript_clamps_an_event_before_the_anchor():
    # Offsets are non-negative in Roark's schema, so a clock skew that puts an
    # event before the anchor must not produce a rejected payload.
    entries = build_transcript([_bot_event("Hello", -2.0)], T0)
    assert entries[0]["startOffsetMs"] == 0


# ───────────────────────────── tool invocations ───────────────────────────


def test_build_tool_invocations_pairs_start_and_end():
    invocations = build_tool_invocations(_events(), T0)

    assert invocations == [
        {
            "name": "get_weather",
            "parameters": {"city": "Lisbon"},
            "result": "sunny",
            "startOffsetMs": 3000,
            "endOffsetMs": 3400,
        }
    ]


def test_build_tool_invocations_reports_a_call_that_never_returned():
    invocations = build_tool_invocations([_tool_start("get_weather", 3.0)], T0)

    assert invocations == [
        {
            "name": "get_weather",
            "parameters": {"city": "Lisbon"},
            "result": "",
            "startOffsetMs": 3000,
        }
    ]


def test_build_tool_invocations_matches_by_tool_call_id():
    events = [
        _tool_start("a", 1.0, tool_call_id="id-a"),
        _tool_start("b", 1.1, tool_call_id="id-b"),
        _tool_end("b", 1.5, tool_call_id="id-b", result="b-done"),
        _tool_end("a", 2.0, tool_call_id="id-a", result="a-done"),
    ]

    invocations = build_tool_invocations(events, T0)

    assert [(i["name"], i["result"]) for i in invocations] == [
        ("a", "a-done"),
        ("b", "b-done"),
    ]


# ───────────────────────────── full payload ───────────────────────────────


def test_build_call_payload_happy_path():
    payload = build_call_payload(
        workflow_run=_workflow_run(),
        definition_id=9,
        recording_url="https://dograh.example/rec?filename=recording.wav",
        agent_id=None,
        agent_name="Support Bot",
        send_transcript=True,
        send_gathered_context=False,
    )

    assert (
        payload["recordingUrl"] == "https://dograh.example/rec?filename=recording.wav"
    )
    assert payload["startedAt"] == T0.isoformat()
    assert payload["interfaceType"] == "PHONE"
    assert payload["callDirection"] == "OUTBOUND"
    assert payload["endedStatus"] == "CUSTOMER_ENDED_CALL"
    assert payload["agent"] == {"name": "Support Bot"}
    assert payload["customer"]["phoneNumberE164"] == "+14155551234"
    assert payload["externalId"] == "dograh-run-4242"
    assert len(payload["transcript"]) == 3
    assert len(payload["toolInvocations"]) == 1


def test_build_call_payload_prefers_an_agent_id():
    payload = build_call_payload(
        workflow_run=_workflow_run(),
        definition_id=None,
        recording_url="https://dograh.example/rec",
        agent_id="6d1e0d6e-0f7a-4f77-9b3f-3a1b2c3d4e5f",
        agent_name="Ignored",
        send_transcript=False,
        send_gathered_context=False,
    )
    assert payload["agent"] == {"roarkId": "6d1e0d6e-0f7a-4f77-9b3f-3a1b2c3d4e5f"}


def test_build_call_payload_omits_the_transcript_when_opted_out():
    payload = build_call_payload(
        workflow_run=_workflow_run(),
        definition_id=None,
        recording_url="https://dograh.example/rec",
        agent_id=None,
        agent_name="Support Bot",
        send_transcript=False,
        send_gathered_context=False,
    )
    assert "transcript" not in payload
    assert "toolInvocations" not in payload


def test_build_call_payload_properties_identify_the_dograh_run():
    payload = build_call_payload(
        workflow_run=_workflow_run(campaign_id=11),
        definition_id=9,
        recording_url="https://dograh.example/rec",
        agent_id=None,
        agent_name="Support Bot",
        send_transcript=True,
        send_gathered_context=False,
    )

    properties = payload["properties"]
    # snake_case, because Roark snake-cases every property name on ingest, so
    # these are the names a user actually filters on.
    assert properties["dograh_workflow_run_id"] == 4242
    assert properties["dograh_workflow_id"] == 7
    assert properties["dograh_workflow_name"] == "Support Bot"
    assert properties["dograh_run_mode"] == "twilio"
    assert properties["dograh_definition_id"] == 9
    assert properties["dograh_campaign_id"] == 11
    assert properties["dograh_call_disposition"] == "user_hangup"
    assert properties["dograh_mapped_call_disposition"] == "appointment_confirmed"
    # Gathered context is opt-in because it routinely holds personal data.
    assert "dograh_context_customer_name" not in properties


def test_gathered_context_does_not_duplicate_the_dispositions():
    payload = build_call_payload(
        workflow_run=_workflow_run(),
        definition_id=None,
        recording_url="https://dograh.example/rec",
        agent_id=None,
        agent_name="Support Bot",
        send_transcript=False,
        send_gathered_context=True,
    )
    properties = payload["properties"]
    assert properties["dograh_call_disposition"] == "user_hangup"
    assert "dograh_context_call_disposition" not in properties
    assert "dograh_context_mapped_call_disposition" not in properties


def test_build_call_payload_includes_gathered_context_when_opted_in():
    payload = build_call_payload(
        workflow_run=_workflow_run(),
        definition_id=None,
        recording_url="https://dograh.example/rec",
        agent_id=None,
        agent_name="Support Bot",
        send_transcript=False,
        send_gathered_context=True,
    )
    assert payload["properties"]["dograh_context_customer_name"] == "Ada"


def test_build_call_payload_falls_back_to_created_at_without_events():
    run = _workflow_run(logs={})
    payload = build_call_payload(
        workflow_run=run,
        definition_id=None,
        recording_url="https://dograh.example/rec",
        agent_id=None,
        agent_name="Support Bot",
        send_transcript=True,
        send_gathered_context=False,
    )
    assert payload["startedAt"] == run.created_at.isoformat()
    assert "transcript" not in payload


# ───────────────────────────── recording URL ──────────────────────────────


async def test_build_recording_url_names_a_wav_so_roark_accepts_it():
    url = await build_recording_url(_completion_context())
    # Roark decides a URL is audio from its shape before fetching it.
    assert url.endswith(
        "/public/download/workflow/tok-123/recording?filename=recording.wav"
    )


async def test_build_recording_url_is_none_without_a_recording():
    context = _completion_context(_workflow_run(recording_url=None))
    assert await build_recording_url(context) is None


async def test_build_recording_url_is_none_without_a_public_token():
    assert await build_recording_url(_completion_context(public_token=None)) is None


async def test_recording_url_prefers_the_tunnel_over_a_local_endpoint():
    """Roark fetches the recording itself, so a laptop or private-network
    deployment has to hand it the cloudflared URL, not localhost."""
    with patch(
        "api.services.integrations.roark.completion.get_backend_endpoints",
        AsyncMock(return_value=("https://sent-anywhere.trycloudflare.com", "wss://x")),
    ):
        url = await build_recording_url(_completion_context())

    assert url.startswith(
        "https://sent-anywhere.trycloudflare.com/api/v1/public/download/"
    )


async def test_recording_url_falls_back_when_no_public_endpoint_resolves():
    """A deployment with neither a public address nor a tunnel still gets a URL
    and a clear refusal from Roark, rather than an exception in the task."""
    with patch(
        "api.services.integrations.roark.completion.get_backend_endpoints",
        AsyncMock(side_effect=ValueError("No tunnel URL available")),
    ):
        url = await build_recording_url(_completion_context())

    assert url is not None
    assert url.endswith("/recording?filename=recording.wav")


# ───────────────────────────── completion handler ─────────────────────────


async def test_completion_posts_the_call_and_reports_the_roark_id():
    delivery = AsyncMock(
        return_value={
            "status": "delivered",
            "status_code": 200,
            "roark_call_id": "cll-1",
            "roark_project_id": "prj-1",
        }
    )

    with patch("api.services.integrations.roark.completion.create_call", delivery):
        results = await run_completion([_roark_node()], _completion_context())

    assert results["roark_roark-1"]["status"] == "delivered"
    assert results["roark_roark-1"]["roark_call_id"] == "cll-1"
    assert results["roark_roark-1"]["external_id"] == "dograh-run-4242"

    _config, payload = delivery.await_args.args
    assert payload["agent"] == {"name": "Support Bot"}
    assert payload["transcript"][0]["role"] == "AGENT"


async def test_completion_skips_a_disabled_node():
    with patch(
        "api.services.integrations.roark.completion.create_call", AsyncMock()
    ) as delivery:
        results = await run_completion(
            [_roark_node(api_key="", roark_enabled=False)], _completion_context()
        )

    assert results == {}
    delivery.assert_not_awaited()


async def test_completion_records_a_validation_failure():
    bad_node = {"id": "roark-1", "type": "roark", "data": {"name": "Roark"}}

    results = await run_completion([bad_node], _completion_context())

    assert results["roark_roark-1"] == {"error": "validation_failed"}


async def test_completion_reports_a_missing_recording():
    context = _completion_context(_workflow_run(recording_url=None))

    results = await run_completion([_roark_node()], context)

    assert results["roark_roark-1"] == {"error": "missing_recording"}


async def test_completion_skips_a_text_chat_run():
    context = _completion_context(_workflow_run(mode="textchat"))

    results = await run_completion([_roark_node()], context)

    assert results["roark_roark-1"]["error"] == "unsupported_run_mode"


async def test_completion_surfaces_a_roark_refusal():
    failing = AsyncMock(
        side_effect=RoarkDeliveryError(
            400, "The provided recording URL is not accessible"
        )
    )

    with patch("api.services.integrations.roark.completion.create_call", failing):
        results = await run_completion([_roark_node()], _completion_context())

    assert results["roark_roark-1"]["status_code"] == 400
    assert "not accessible" in results["roark_roark-1"]["error"]


async def test_completion_handles_two_nodes_independently():
    delivery = AsyncMock(
        side_effect=[
            {"status": "delivered", "status_code": 200, "roark_call_id": "cll-1"},
            RoarkDeliveryError(401, "Invalid API key"),
        ]
    )

    with patch("api.services.integrations.roark.completion.create_call", delivery):
        results = await run_completion(
            [_roark_node("roark-1"), _roark_node("roark-2")], _completion_context()
        )

    assert results["roark_roark-1"]["status"] == "delivered"
    assert results["roark_roark-2"]["status_code"] == 401


# ───────────────────────────── client ─────────────────────────────────────


async def test_create_call_returns_the_created_identifiers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/call"
        assert request.headers["authorization"] == "Bearer rk_live_x"
        return httpx.Response(200, json={"data": {"id": "cll-1", "projectId": "prj-1"}})

    with _roark_http(handler):
        result = await create_call(
            RoarkDeliveryConfig(base_url="https://api.roark.ai", api_key="rk_live_x"),
            {"externalId": "dograh-run-1"},
        )

    assert result["status"] == "delivered"
    assert result["roark_call_id"] == "cll-1"
    assert result["roark_project_id"] == "prj-1"


async def test_create_call_trims_a_trailing_slash_from_the_base_url():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": {"id": "cll-1"}})

    with _roark_http(handler):
        await create_call(
            RoarkDeliveryConfig(base_url="https://api.roark.ai/", api_key="rk_live_x"),
            {"externalId": "dograh-run-1"},
        )

    assert seen == ["https://api.roark.ai/v1/call"]


async def test_create_call_reports_a_redelivered_run_as_a_duplicate():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "message": "A call with this externalId already exists in this project"
            },
        )

    with _roark_http(handler):
        result = await create_call(
            RoarkDeliveryConfig(base_url="https://api.roark.ai", api_key="rk_live_x"),
            {"externalId": "dograh-run-1"},
        )

    # The post-call job can run twice for one run, and the second time Roark
    # already holds the call. That is not a failure.
    assert result == {"status": "duplicate", "status_code": 409}


async def test_create_call_raises_with_roarks_own_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"code": "validation_error", "message": "recordingUrl is required"},
        )

    with _roark_http(handler):
        with pytest.raises(RoarkDeliveryError) as excinfo:
            await create_call(
                RoarkDeliveryConfig(
                    base_url="https://api.roark.ai", api_key="rk_live_x"
                ),
                {"externalId": "dograh-run-1"},
            )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "recordingUrl is required"


async def test_create_call_falls_back_to_the_body_when_there_is_no_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="upstream unavailable")

    with _roark_http(handler):
        with pytest.raises(RoarkDeliveryError) as excinfo:
            await create_call(
                RoarkDeliveryConfig(
                    base_url="https://api.roark.ai", api_key="rk_live_x"
                ),
                {"externalId": "dograh-run-1"},
            )

    assert excinfo.value.status_code == 502
    assert "upstream unavailable" in excinfo.value.detail


def test_delivery_config_rejects_a_blank_api_key():
    with pytest.raises(ValidationError):
        RoarkDeliveryConfig(base_url="https://api.roark.ai", api_key="  ")
