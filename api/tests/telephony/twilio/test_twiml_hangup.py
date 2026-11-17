"""The connect-stream TwiML must end the call once the media stream closes.

``<Connect>`` blocks until the stream closes and then continues with the next
verb. When that verb was ``<Pause length="40"/>`` the caller was left on a
silent, still-connected line for 40 seconds after the agent hung up (#627).
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.telephony.providers.twilio.provider import TwilioProvider

WS_URL = "wss://example.test/api/v1/telephony/twilio/ws/7/11/123/tok"


def _provider() -> TwilioProvider:
    return TwilioProvider(
        {
            "account_sid": "AC123",
            "auth_token": "twilio-auth-token",
            "from_numbers": ["+15551230002"],
        }
    )


def _assert_hangs_up(twiml: str) -> None:
    assert "<Connect>" in twiml
    assert "<Pause" not in twiml
    assert twiml.rstrip().endswith("<Hangup/>\n</Response>")


@pytest.mark.asyncio
async def test_outbound_twiml_hangs_up_when_stream_closes():
    with (
        patch(
            "api.services.telephony.providers.twilio.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://example.test", "wss://example.test"),
        ),
        patch(
            "api.services.telephony.ws_auth.build_media_ws_url",
            return_value=WS_URL,
        ),
    ):
        twiml = await _provider().get_webhook_response(
            workflow_id=7, organization_id=11, workflow_run_id=123
        )

    _assert_hangs_up(twiml)


@pytest.mark.asyncio
async def test_inbound_twiml_hangs_up_when_stream_closes():
    response = await _provider().start_inbound_stream(
        websocket_url=WS_URL,
        workflow_run_id=123,
        normalized_data=None,
        backend_endpoint="https://example.test",
    )

    _assert_hangs_up(response.body.decode())
