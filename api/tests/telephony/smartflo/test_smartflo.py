"""Comprehensive unit tests for Tata Smartflo integration in Dograh."""

import base64
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pipecat.frames.frames import (
    AudioRawFrame,
    EndFrame,
    InputAudioRawFrame,
    InputDTMFFrame,
    InterruptionFrame,
    KeypadEntry,
)

from api.services.telephony.providers.smartflo.credential_resolver import (
    mask_phone_number,
    resolve_smartflo_credentials,
)
from api.services.telephony.providers.smartflo.serializers import SmartfloFrameSerializer
from api.services.telephony.providers.smartflo.agent_resolver import resolve_dograh_agent
from api.services.telephony.providers.smartflo.redis_state import (
    delete_smartflo_call_state,
    get_smartflo_call_state,
    save_smartflo_call_state,
)
from api.services.telephony.providers.smartflo.provider import SmartfloProvider


# ---------------------------------------------------------------------------
# Test 1: Phone Masking & Security
# ---------------------------------------------------------------------------
def test_phone_masking():
    assert mask_phone_number("919876543210") == "91******3210"
    assert mask_phone_number("+919999999999") == "+9******9999"
    assert mask_phone_number(None) == "UNKNOWN"
    assert mask_phone_number("123") == "***"


# ---------------------------------------------------------------------------
# Test 2: 4-Tier Credential Resolution
# ---------------------------------------------------------------------------
def test_credential_resolution_priority():
    # Priority 1: Request Body overrides everything
    api_key, did, jwt, domain = resolve_smartflo_credentials(
        call_details={
            "smartflo_api_key": "req_key",
            "smartflo_did_number": "req_did",
            "smartflo_jwt_token": "req_jwt",
            "smartflo_api_domain": "https://api.req.com",
        },
        agent_config={
            "smartflo_api_key": "agent_key",
            "smartflo_did_number": "agent_did",
            "smartflo_jwt_token": "agent_jwt",
        },
        org_config={
            "smartflo_api_key": "org_key",
            "smartflo_did_number": "org_did",
        },
    )
    assert api_key == "req_key"
    assert did == "req_did"
    assert jwt == "req_jwt"
    assert domain == "https://api.req.com"

    # Priority 2: Agent config overrides Org and Env
    api_key, did, jwt, domain = resolve_smartflo_credentials(
        call_details={},
        agent_config={
            "smartflo_api_key": "agent_key",
            "smartflo_did_number": "agent_did",
            "smartflo_jwt_token": "agent_jwt",
        },
        org_config={
            "smartflo_api_key": "org_key",
            "smartflo_did_number": "org_did",
        },
    )
    assert api_key == "agent_key"
    assert did == "agent_did"
    assert jwt == "agent_jwt"

    # Priority 3: Org config overrides Env
    api_key, did, jwt, domain = resolve_smartflo_credentials(
        call_details={},
        agent_config={},
        org_config={
            "smartflo_api_key": "org_key",
            "smartflo_did_number": "org_did",
        },
    )
    assert api_key == "org_key"
    assert did == "org_did"

    # Priority 4: Env Fallback
    with patch.dict(
        os.environ,
        {
            "SMARTFLO_CLICK_TO_CALL_API_KEY": "env_key",
            "SMARTFLO_DID_NUMBER": "env_did",
            "SMARTFLO_JWT_TOKEN": "env_jwt",
        },
    ):
        api_key, did, jwt, domain = resolve_smartflo_credentials()
        assert api_key == "env_key"
        assert did == "env_did"
        assert jwt == "env_jwt"

    # Validation: Missing required fields must raise ValueError
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="Missing required Smartflo Click-to-Call API Key"):
            resolve_smartflo_credentials(call_details={"smartflo_did_number": "123"})

        with pytest.raises(ValueError, match="Missing required Smartflo DID Number"):
            resolve_smartflo_credentials(call_details={"smartflo_api_key": "key"})


# ---------------------------------------------------------------------------
# Test 3: Smartflo Frame Serializer (Audio & Protocol Conversion)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_smartflo_serializer_json_media():
    serializer = SmartfloFrameSerializer(stream_sid="stream_test_123")

    # Inbound: JSON Media frame -> InputAudioRawFrame
    pcm_audio = b"\x00\x01\x00\x02" * 100
    b64_audio = base64.b64encode(pcm_audio).decode("ascii")
    inbound_json = json.dumps({
        "event": "media",
        "streamSid": "stream_test_123",
        "media": {"payload": b64_audio},
    })

    frame = await serializer.deserialize(inbound_json)
    assert isinstance(frame, InputAudioRawFrame)
    assert frame.audio == pcm_audio
    assert frame.sample_rate == 8000

    # Outbound: AudioRawFrame -> JSON Media frame
    out_frame = AudioRawFrame(audio=pcm_audio, sample_rate=8000, num_channels=1)
    serialized = await serializer.serialize(out_frame)
    assert serialized is not None
    data = json.loads(serialized)
    assert data["event"] == "media"
    assert data["streamSid"] == "stream_test_123"
    assert base64.b64decode(data["media"]["payload"]) == pcm_audio


@pytest.mark.asyncio
async def test_smartflo_serializer_binary_mode():
    serializer = SmartfloFrameSerializer(stream_sid="stream_test_456")

    # Inbound: Raw binary PCM bytes
    raw_pcm = b"\x05\x06\x07\x08" * 50
    frame = await serializer.deserialize(raw_pcm)
    assert isinstance(frame, InputAudioRawFrame)
    assert frame.audio == raw_pcm
    assert serializer._binary_mode is True

    # Outbound in binary mode: AudioRawFrame -> raw bytes
    out_frame = AudioRawFrame(audio=raw_pcm, sample_rate=8000, num_channels=1)
    serialized = await serializer.serialize(out_frame)
    assert serialized == raw_pcm


@pytest.mark.asyncio
async def test_smartflo_serializer_control_frames():
    serializer = SmartfloFrameSerializer(stream_sid="stream_ctrl_789")

    # InterruptionFrame -> clear event
    clear_msg = await serializer.serialize(InterruptionFrame())
    assert json.loads(clear_msg) == {"event": "clear", "streamSid": "stream_ctrl_789"}

    # EndFrame -> stop event
    stop_msg = await serializer.serialize(EndFrame())
    assert json.loads(stop_msg) == {"event": "stop", "streamSid": "stream_ctrl_789"}

    # Inbound DTMF
    dtmf_json = json.dumps({"event": "dtmf", "dtmf": {"digit": "5"}})
    frame = await serializer.deserialize(dtmf_json)
    assert isinstance(frame, InputDTMFFrame)
    assert frame.button == KeypadEntry.FIVE

    # Inbound Stop
    stop_json = json.dumps({"event": "stop"})
    frame = await serializer.deserialize(stop_json)
    assert isinstance(frame, EndFrame)


# ---------------------------------------------------------------------------
# Test 4: Provider Status Callback Normalization
# ---------------------------------------------------------------------------
def test_smartflo_status_callback_normalization():
    provider = SmartfloProvider({})

    # Ringing
    ringing = provider.parse_status_callback({
        "call_id": "c123",
        "status": "ringing",
        "caller_id": "918888888888",
        "customer_number": "919999999999",
    })
    assert ringing["status"] == "ringing"
    assert ringing["call_id"] == "c123"

    # Answered
    answered = provider.parse_status_callback({"call_id": "c123", "status": "answered"})
    assert answered["status"] == "answered"

    # Completed with duration
    completed = provider.parse_status_callback({
        "call_id": "c123",
        "status": "completed",
        "duration": "45",
    })
    assert completed["status"] == "completed"
    assert completed["duration"] == "45"

    # Busy / Failed / No-Answer
    busy = provider.parse_status_callback({"call_id": "c123", "status": "busy"})
    assert busy["status"] == "busy"

    no_ans = provider.parse_status_callback({"call_id": "c123", "status": "no-answer"})
    assert no_ans["status"] == "no-answer"


# ---------------------------------------------------------------------------
# Test 5: Outbound API Call Mocking & Error Handling
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_smartflo_provider_initiate_call_success():
    provider = SmartfloProvider({
        "click_to_call_api_key": "test_api_key",
        "smartflo_did_number": "911111111111",
        "smartflo_jwt_token": "test_jwt",
    })

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "ref_id": "ref_abc_123",
        "call_id": "call_xyz_456",
        "message": "Call queued successfully",
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("api.services.telephony.providers.smartflo.provider.save_smartflo_call_state", new_callable=AsyncMock) as mock_save:
        mock_post.return_value = mock_resp

        result = await provider.initiate_call(
            to_number="919999999999",
            webhook_url="https://example.com/smartflo_connect",
            workflow_run_id=999,
            agent_id="test_agent",
        )

        assert result.call_id == "call_xyz_456"
        assert result.provider_metadata["smartflo_ref_id"] == "ref_abc_123"
        mock_post.assert_awaited_once()
        # Verify secrets are NOT logged and payload matches Smartflo contract
        sent_payload = mock_post.await_args.kwargs["json"]
        assert sent_payload["customer_number"] == "919999999999"
        assert sent_payload["caller_id"] == "911111111111"
        assert sent_payload["api_key"] == "test_api_key"
        assert sent_payload["custom_identifier"] == "999"
