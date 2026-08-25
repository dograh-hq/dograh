"""Tests for recovering an inbound call's SIP Call-ID for Tuner.

Tuner links a call Dograh reports back to the simulation that placed it by
matching the caller's SIP Call-ID. Cloudonix keeps that value off the
TwiML-compatible fields but exposes it on the session object, so it is read
from there.

Covers:
- The payload shape Cloudonix actually delivers (captured from live calls)
- All three places Cloudonix repeats the Call-ID, and their precedence
- The correlation-header fallback, for a provider that forwards no Call-ID
- That only correlation identifiers are exported, never provider or tenant metadata
- Every shape carrying nothing usable, which must stay silent rather than raise
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from api.services.integrations.tuner.collector import (
    DeferredTunerObserver,
    extract_inbound_sip_metadata,
)

# Verbatim from a live Tuner simulation call, as delivered by Cloudonix to
# /inbound/run. ``CID`` carries the caller's SIP Call-ID, repeated by Cloudonix
# in SessionData.callIds and profile.callId; ``Correlation-Id`` is Tuner's own
# marker, arriving with the ``X-`` prefix stripped by Cloudonix.
LIVE_CALL_ID = "B3tExKsHfHt96HSkBX96iOaeH9fB"
LIVE_CORRELATION_ID = "6c38334da5ca4c868cc9ee0ba2a2b852"

LIVE_TRUNK_SIP_HEADERS = {
    "CID": LIVE_CALL_ID,
    "LiveKit-Room": "sim-6c38334d",
    "Correlation-Id": LIVE_CORRELATION_ID,
    "Cloudonix-Origin": "border",
    "Cloudonix-IP": "161.115.178.174",
    "Cloudonix-Signature": "f1c57c099e4b90a8476a389c958001e5",
}

LIVE_SESSION_DATA = {
    "id": 30894740,
    "token": "1f2fc84f9a79400a90c19830d38d8a11",
    "status": "NEW",
    "callIds": [LIVE_CALL_ID],
    "profile": {
        "callId": [LIVE_CALL_ID],
        "inbound-trunk-id": 0,
        "trunk-sip-headers": LIVE_TRUNK_SIP_HEADERS,
    },
    "destination": "9001",
}


def _run(session_data: Any) -> SimpleNamespace:
    """A workflow run whose stored inbound webhook carries ``session_data``."""
    return SimpleNamespace(
        logs={"inbound_webhook": {"raw_webhook_data": {"SessionData": session_data}}}
    )


def test_extracts_call_id_from_live_cloudonix_payload():
    sip_call_id, headers = extract_inbound_sip_metadata(_run(LIVE_SESSION_DATA))

    assert sip_call_id == LIVE_CALL_ID
    # Only correlation identifiers are reported, which is enough to show which
    # of them the provider forwarded when a call fails to link.
    assert headers == {"CID": LIVE_CALL_ID, "Correlation-Id": LIVE_CORRELATION_ID}


def test_provider_and_tenant_metadata_is_never_exported():
    """Nothing beyond correlation identifiers may leave the deployment.

    ``trunk-sip-headers`` also carries provider authentication, source
    addresses, and upstream account ids. Tuner correlates on none of them.
    """
    _, headers = extract_inbound_sip_metadata(
        _run(
            {
                "profile": {
                    "trunk-sip-headers": {
                        "CID": LIVE_CALL_ID,
                        "Cloudonix-Signature": "f1c57c099e4b90a8476a389c958001e5",
                        "Cloudonix-IP": "161.115.178.174",
                        "Cloudonix-Port": "9000",
                        "Cloudonix-Timestamp": "1787188329",
                        "Twilio-AccountSid": "AC00000000000000000000000000000000",
                        "LiveKit-Room": "sim-6c38334d",
                    }
                }
            }
        )
    )

    assert headers == {"CID": LIVE_CALL_ID}


def test_call_id_wins_over_the_correlation_header():
    """The Call-ID is the identifier Tuner matches on; ours is only a fallback."""
    sip_call_id, _ = extract_inbound_sip_metadata(_run(LIVE_SESSION_DATA))
    assert sip_call_id == LIVE_CALL_ID
    assert sip_call_id != LIVE_CORRELATION_ID


@pytest.mark.parametrize(
    "session_data",
    [
        pytest.param({"callIds": [LIVE_CALL_ID]}, id="session_callIds"),
        pytest.param({"profile": {"callId": [LIVE_CALL_ID]}}, id="profile_callId"),
        pytest.param(
            {"profile": {"trunk-sip-headers": {"CID": LIVE_CALL_ID}}},
            id="cid_header",
        ),
        pytest.param({"callIds": LIVE_CALL_ID}, id="callIds_as_bare_string"),
    ],
)
def test_reads_the_call_id_from_any_of_its_locations(session_data: Any):
    sip_call_id, _ = extract_inbound_sip_metadata(_run(session_data))
    assert sip_call_id == LIVE_CALL_ID


@pytest.mark.parametrize(
    "name", ["Correlation-Id", "X-Correlation-Id", "CORRELATION-ID", "Call-ID"]
)
def test_falls_back_to_a_correlation_header_when_no_call_id(name: str):
    """Cloudonix strips ``X-``; another provider may forward it untouched."""
    sip_call_id, _ = extract_inbound_sip_metadata(
        _run({"profile": {"trunk-sip-headers": {name: "corr-1"}}})
    )
    assert sip_call_id == "corr-1"


def test_reads_subscriber_origin_headers():
    sip_call_id, _ = extract_inbound_sip_metadata(
        _run({"profile": {"subscriber-sip-headers": {"CID": "sub-1"}}})
    )
    assert sip_call_id == "sub-1"


def test_a_call_with_no_correlation_headers_reports_nothing():
    """Headers that are not correlation identifiers must not be reported."""
    assert extract_inbound_sip_metadata(
        _run({"profile": {"trunk-sip-headers": {"Cloudonix-Origin": "border"}}})
    ) == (None, None)


@pytest.mark.parametrize(
    "logs",
    [
        pytest.param(None, id="no_logs"),
        pytest.param({}, id="empty_logs"),
        pytest.param({"inbound_webhook": {}}, id="webhook_without_body"),
        pytest.param(
            {"inbound_webhook": {"raw_webhook_data": {"CallSid": "abc"}}},
            id="webhook_without_session_data",
        ),
    ],
)
def test_absent_or_unrelated_shapes_yield_nothing(logs: Any):
    """Outbound, web, and ordinary carrier calls must pass through untouched."""
    assert extract_inbound_sip_metadata(SimpleNamespace(logs=logs)) == (None, None)


@pytest.mark.parametrize(
    "session_data",
    [
        pytest.param({"token": "t"}, id="session_without_profile"),
        pytest.param({"profile": "not-a-dict"}, id="profile_is_str"),
        pytest.param(
            {"profile": {"trunk-sip-headers": "not-a-dict"}}, id="headers_are_str"
        ),
        pytest.param({"callIds": []}, id="callIds_empty"),
        pytest.param({"callIds": [None]}, id="callIds_holds_null"),
    ],
)
def test_malformed_session_data_does_not_raise(session_data: Any):
    """A provider shape we did not anticipate must not fail the call."""
    assert extract_inbound_sip_metadata(_run(session_data)) == (None, None)


def test_identifier_reaches_the_payload_delivered_to_tuner():
    """The end of the chain: what the observer snapshots is what Tuner receives."""
    sip_call_id, sip_headers = extract_inbound_sip_metadata(_run(LIVE_SESSION_DATA))
    observer = DeferredTunerObserver(
        workflow_run_id=42,
        call_type="phone_call",
        sip_call_id=sip_call_id,
        sip_headers=sip_headers,
    )

    payload = observer.build_payload_snapshot()

    assert payload["sip_call_id"] == LIVE_CALL_ID
    assert payload["sip_headers"] == {
        "CID": LIVE_CALL_ID,
        "Correlation-Id": LIVE_CORRELATION_ID,
    }


def test_call_without_an_identifier_omits_both_fields():
    """Production traffic must be delivered exactly as it is today."""
    payload = DeferredTunerObserver(
        workflow_run_id=43, call_type="phone_call"
    ).build_payload_snapshot()

    assert "sip_call_id" not in payload
    assert "sip_headers" not in payload
