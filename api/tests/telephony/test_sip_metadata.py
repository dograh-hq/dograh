"""Provider webhook normalization keeps carrier and SIP identifiers separate."""

import pytest

from api.services.telephony.providers.cloudonix.provider import CloudonixProvider
from api.services.telephony.providers.twilio.provider import TwilioProvider

SIP_CALL_ID = "B3tExKsHfHt96HSkBX96iOaeH9fB"
CORRELATION_ID = "6c38334da5ca4c868cc9ee0ba2a2b852"


def _cloudonix(session_data):
    return CloudonixProvider.parse_inbound_webhook(
        {"CallSid": "cloudonix-session", "SessionData": session_data}
    )


def test_cloudonix_normalizes_live_session_shape():
    normalized = _cloudonix(
        {
            "token": "cloudonix-session",
            "callIds": [SIP_CALL_ID],
            "profile": {
                "callId": [SIP_CALL_ID],
                "trunk-sip-headers": {
                    "CID": SIP_CALL_ID,
                    "Correlation-Id": CORRELATION_ID,
                    "LiveKit-Room": "sim-6c38334d",
                    "Cloudonix-Signature": "provider-signature",
                    "Twilio-AccountSid": "AC123",
                },
            },
        }
    )

    assert normalized.call_id == "cloudonix-session"
    assert normalized.sip_call_id == SIP_CALL_ID
    assert normalized.sip_headers["Correlation-Id"] == CORRELATION_ID
    # Normalization retains diagnostic headers; integrations own export filtering.
    assert normalized.sip_headers["Cloudonix-Signature"] == "provider-signature"
    assert normalized.raw_data["underlying_provider"] == "twilio"


@pytest.mark.parametrize(
    "session_data",
    [
        {"callIds": [SIP_CALL_ID]},
        {"callIds": [None, "", SIP_CALL_ID]},
        {"callIds": SIP_CALL_ID},
        {"profile": {"callId": [SIP_CALL_ID]}},
        {"profile": {"trunk-sip-headers": {"CID": SIP_CALL_ID}}},
        {"profile": {"subscriber-sip-headers": {"CID": SIP_CALL_ID}}},
    ],
)
def test_cloudonix_reads_all_call_id_locations(session_data):
    assert _cloudonix(session_data).sip_call_id == SIP_CALL_ID


@pytest.mark.parametrize(
    "name", ["Call-ID", "Correlation-Id", "X-Correlation-Id", "CORRELATION-ID"]
)
def test_cloudonix_preserves_header_fallback(name):
    normalized = _cloudonix({"profile": {"trunk-sip-headers": {name: CORRELATION_ID}}})
    assert normalized.sip_call_id == CORRELATION_ID


def test_cloudonix_prefers_session_call_id_then_profile_then_headers():
    profile = {"callId": ["profile-id"], "trunk-sip-headers": {"CID": "header-id"}}
    assert (
        _cloudonix({"callIds": [SIP_CALL_ID], "profile": profile}).sip_call_id
        == SIP_CALL_ID
    )
    assert _cloudonix({"profile": profile}).sip_call_id == "profile-id"


def test_cloudonix_keeps_first_non_empty_header_across_origins_and_casing():
    normalized = _cloudonix(
        {
            "profile": {
                "trunk-sip-headers": {"CID": "", "Correlation-Id": CORRELATION_ID},
                "subscriber-sip-headers": {
                    "cid": [None, "", SIP_CALL_ID],
                    "correlation-id": "other",
                },
            }
        }
    )
    assert normalized.sip_call_id == SIP_CALL_ID
    assert normalized.sip_headers == {
        "Correlation-Id": CORRELATION_ID,
        "cid": SIP_CALL_ID,
    }


@pytest.mark.parametrize(
    "session_data",
    [
        None,
        "not-a-dict",
        {},
        {"token": "session"},
        {"profile": "not-a-dict"},
        {"profile": {"trunk-sip-headers": "not-a-dict"}},
        {"callIds": []},
        {"callIds": [None]},
    ],
)
def test_cloudonix_absent_or_malformed_sip_data_does_not_break_normalization(
    session_data,
):
    normalized = _cloudonix(session_data)
    assert normalized.sip_call_id is None
    assert normalized.sip_headers == {}


def test_twilio_normalizes_live_sip_domain_webhook():
    webhook = {
        "SipDomain": "tuner-sim-test.sip.twilio.com",
        "ApiVersion": "2010-04-01",
        "From": "sip:+15550001111@project.sip.livekit.cloud",
        "To": "sip:tuner-sim@tuner-sim-test.sip.twilio.com",
        "Direction": "inbound",
        "AccountSid": "ACxxxxxxxx",
        "SipDomainSid": "SDxxxxxxxx",
        "SipCallId": SIP_CALL_ID,
        "CallSid": "CA8e7407250cb9282ca05d6f5c43f1872e",
        "SipSourceIp": "203.0.113.10",
        "SipHeader_X-Correlation-Id": CORRELATION_ID,
        "SipHeader_X-LiveKit-Room": "sim-6c38334d",
    }
    normalized = TwilioProvider.parse_inbound_webhook(webhook)
    assert normalized.call_id == webhook["CallSid"]
    assert normalized.sip_call_id == SIP_CALL_ID
    assert normalized.sip_headers == {
        "X-Correlation-Id": CORRELATION_ID,
        "X-LiveKit-Room": "sim-6c38334d",
    }
    assert normalized.raw_data == webhook


@pytest.mark.parametrize("sip_call_id", [None, "", [None, ""], 42])
def test_twilio_does_not_use_correlation_marker_as_call_id(sip_call_id):
    normalized = TwilioProvider.parse_inbound_webhook(
        {
            "SipCallId": sip_call_id,
            "SipHeader_X-Correlation-Id": CORRELATION_ID,
        }
    )
    assert normalized.sip_call_id is None
    assert normalized.sip_headers == {"X-Correlation-Id": CORRELATION_ID}


@pytest.mark.parametrize(
    "webhook",
    [
        {
            "CallSid": "CA123",
            "From": "+15550001111",
            "To": "+15550002222",
            "Direction": "inbound",
        },
        {"SessionData": {"callIds": [SIP_CALL_ID]}},
    ],
)
def test_twilio_without_sip_fields_reports_nothing(webhook):
    normalized = TwilioProvider.parse_inbound_webhook(webhook)
    assert normalized.sip_call_id is None
    assert normalized.sip_headers == {}
